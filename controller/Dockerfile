FROM python:3.10
ADD /kopf /kopf
RUN pip install kopf kubernetes psycopg2 boto3
CMD kopf run --liveness=http://0.0.0.0:8080/healthz /kopf/handlers.py --verbose --all-namespaces