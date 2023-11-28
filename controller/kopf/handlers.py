import json
import kopf
import os
import psycopg2
import boto3
import secrets
from kubernetes import client, config
import time

DB_HOST = os.getenv("DB_HOST")
DB_USERNAME = os.getenv("DB_USERNAME")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DOMAIN = os.getenv("DOMAIN")
SSM_PREFIX = os.getenv("SSM_PREFIX", default="/dpc")

OBJECT_TYPES = [
    "TABLES",
    "SEQUENCES",
    "FUNCTIONS",
]

config.load_incluster_config()
api_client = client.CustomObjectsApi()

def get_cursor(database):
    conn = psycopg2.connect(host=DB_HOST, port=5432, dbname=database, user=DB_USERNAME,
                            password=DB_PASSWORD, target_session_attrs="read-write", sslmode='require')
    conn.autocommit = True
    return conn.cursor()

def set_annotation(name, namespace,value):
    api_client.patch_namespaced_custom_object(
        group=DOMAIN,
        version="v1",
        name=name,
        namespace=namespace,
        plural='dpcdatabases',
        body={ "metadata": {"annotations": { f"{DOMAIN}/database-created": value}}}
    )

def set_existing_database(name, namespace):
    api_client.patch_namespaced_custom_object(
        group=DOMAIN,
        version="v1",
        name=name,
        namespace=namespace,
        plural='dpcdatabases',
        body={ "metadata": {"annotations": { f"{DOMAIN}/database-imported": "True"}}}
    )

def generate_password():
    password = secrets.token_hex(15 // 2)
    return password[:15]

def create_ssm(name, namespace, password):
    ssm_name = f"{SSM_PREFIX}/{name}/password"
    client = boto3.client('ssm')
    client.put_parameter(
        Name=ssm_name,
        Description=f"Rds Password for {name}",
        Value=password,
        Type='SecureString',
        Tags=[
            {
                'Key': 'provisioned-by',
                'Value': 'database-provisioner-controller'
            },
            {
                'Key': 'namespace',
                'Value': namespace
            },
        ],
        Tier='Standard',
    )

def delete_ssm(name):
    ssm_name = f"{SSM_PREFIX}/{name}/password"
    client = boto3.client('ssm')
    try:
        client.delete_parameter(Name=ssm_name)
    except:
        print("Nevermind, just tried")

def review_schema(body,spec, name, namespace, schema_name):
    cur = get_cursor("postgres")
    try:
        cur.execute(f"SELECT FROM pg_database WHERE datname = '{schema_name}'")
        if cur.fetchone() != None:
            kopf.warn(body, reason='Reject', message=f"Database {schema_name} already exists.")
            set_existing_database(name, namespace)
        else:
            kopf.info(body, reason='Proceed', message=f"Creating {schema_name}.")
            cur.execute(f"CREATE DATABASE {schema_name}")

        random_password = generate_password()

        cur.execute(f"CREATE ROLE {schema_name} LOGIN PASSWORD '{random_password}'")
        cur.execute(f"GRANT {schema_name} to {DB_USERNAME}")
        cur.execute(f"GRANT CONNECT ON DATABASE {schema_name} TO {schema_name}")
        cur.close()
    except:
        set_annotation(name, namespace,"error")
        cur.close()
        return
    
    cur_schema = get_cursor(schema_name)
    try:
        cur_schema.execute(f"GRANT CREATE, USAGE ON SCHEMA public TO {schema_name}")

        for type in OBJECT_TYPES:
            cur_schema.execute(f"GRANT ALL PRIVILEGES ON ALL {type} IN SCHEMA public TO {schema_name}")
            cur_schema.execute(f"""ALTER DEFAULT PRIVILEGES FOR ROLE {schema_name} IN SCHEMA public
            GRANT ALL PRIVILEGES ON {type} TO {schema_name}""")
        cur_schema.close()
    except:
        cur_schema.close()
        set_annotation(name, namespace,"error")
        return

    if spec.get("create_ssm", False):
        kopf.info(body, reason='Proceed', message=f"Creating SSM Parameter.")
        create_ssm(name, namespace, random_password)

    set_annotation(name, namespace,"true")


def delete_schema(name, namespace, schema_name, drop_on_delete):
    cur = get_cursor("postgres")
    cur_schema = get_cursor(schema_name)

    cur.execute(f"REVOKE ALL ON DATABASE {schema_name} FROM {schema_name}")
    time.sleep(1)
    cur_schema.execute(f"REASSIGN OWNED BY {schema_name} TO {DB_USERNAME}")
    time.sleep(1)
    cur_schema.execute(f"DROP OWNED BY {schema_name}")
    time.sleep(1)
    cur_schema.execute(f"REVOKE ALL ON SCHEMA public FROM {schema_name}")
    time.sleep(1)

    for type in OBJECT_TYPES:
        cur_schema.execute(
            f"REVOKE ALL PRIVILEGES ON ALL {type} IN SCHEMA public FROM {schema_name}")

    cur_schema.close()
    time.sleep(3)
    cur.execute(f"select pg_terminate_backend(pid) from pg_stat_activity where datname='{schema_name}'")
    
    if drop_on_delete == True:
        time.sleep(1)
        cur.execute(f"DROP DATABASE IF EXISTS {schema_name}")
        
    time.sleep(1)
    cur.execute(f"DROP ROLE IF EXISTS {schema_name}")
    cur.close()

    delete_ssm(name)


@kopf.on.create('dpcdatabases')
def create_fn(body, name, spec, namespace, logger, **kwargs):
    kopf.info(body, reason='NewDatabase',
              message="Received a database creation job")
    
    schema_name = spec.get("schema_name", False)

    if "annotations" in body['metadata'] and f"{DOMAIN}/database-created" in body['metadata']['annotations'] and body['metadata']['annotations'][f"{DOMAIN}/database-created"] == "true": 
        kopf.warn(body, reason='Rejected',
              message=f"Database {schema_name} already provisioned.")
        return
    else:
        kopf.info(body, reason='Provisioning',
              message=f"Provisioning {schema_name}.")
        review_schema(body,spec,name, namespace, schema_name)


@kopf.on.delete('dpcdatabases')
def create_fn(body, name, spec, namespace, logger, **kwargs):
    schema_name = spec.get("schema_name")
    drop_on_delete = spec.get("drop_on_delete", False)

    # Override to false if the object was an existing imported database
    if "annotations" in body['metadata'] and f"{DOMAIN}/database-imported" in body['metadata']['annotations'] and body['metadata']['annotations'][f"{DOMAIN}/database-imported"] == "True": 
        drop_on_delete = False
        
    delete_schema( name, namespace, schema_name, drop_on_delete)
