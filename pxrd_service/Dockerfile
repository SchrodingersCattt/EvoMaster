FROM registry.dp.tech/public/python:3.13-slim

ENV PIP_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple/ \
	PIP_TRUSTED_HOST=mirrors.tuna.tsinghua.edu.cn \
	PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app/pxrd_service/

# Use an unmapped fixed UID so this layer does not mutate /etc/passwd, /etc/group,
# or /etc/gshadow. The service only writes request-scoped files under /tmp.
USER 10001:10001

EXPOSE 8010

CMD ["python", "-m", "uvicorn", "pxrd_service.service:app", "--host", "0.0.0.0", "--port", "8010"]
