#!/bin/sh
# Runs inside the sandbox container: start the mock API on loopback, run pytest, print a JUnit report.
python /mock/mock_server.py --version "${MOCK_VERSION:-v2}" --port "${MOCK_PORT:-8088}" &
for i in $(seq 1 50); do
  python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:${MOCK_PORT:-8088}/health', timeout=0.2)" 2>/dev/null && break
  sleep 0.1
done
timeout 55 python -m pytest -q -p no:cacheprovider --junitxml=/tmp/report.xml
code=$?
echo "PYTEST_EXIT=$code"
echo "===APISHIFT_JUNIT==="
cat /tmp/report.xml 2>/dev/null
exit 0
