# k8s-postgres-database-provisioner-controller
This is a simple controller for dinamically provisioning a database in a postgres instance. Is my first contribution to the communtity so please bear with me 😊.

## Documentation

I'll do my best to include it in readthedocs.com

## Features

- Uses [psycopg2](https://pypi.org/project/psycopg2/) as the library for connecting to the database.
- Uses [boto3](https://pypi.org/project/boto3/) for accessing AWS API.
- Creates a database, an user, and creates a password for it, with this definition
- It create an SSM parameter with the credentials.
- If you set `drop_on_delete: false` it reassign all the things to the user specified in `DB_USERNAME` and drop the dinamically created user keeping the database.

## Configuration

### Namespace

Create a namespace

```sh
$ kubectl create namespace database-provisioner-controller
```

If you dont want that name, specify your own and change the value global.namespace

### Secret with the postgres credentials

There should be a secret in the namespace with the following structure

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: database-provisioner-controller
  namespace: database-provisioner-controller
data:
 DB_HOST: <base64 encoded postgres host>
 DB_USERNAME: <base64 encoded postgres username with admin privileges>
 DB_PASSWORD: <base64 encoded postgres password>
```