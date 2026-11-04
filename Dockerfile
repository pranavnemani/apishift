# Sandbox image for running a target repo's tests. Build once:
#   docker build -t apishift-sandbox:latest .
FROM python:3.12-slim
RUN pip install --no-cache-dir "requests>=2.31" "pydantic>=2.6" "pytest>=8.0"
COPY sandbox/run-tests.sh /opt/apishift/run-tests.sh
RUN chmod 755 /opt/apishift/run-tests.sh && useradd -u 10001 -M sandbox
USER 10001
