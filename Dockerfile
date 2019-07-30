FROM alpine:latest
RUN apk add --no-cache build-base libffi-dev openssl-dev python3-dev && \
    if [ ! -e /usr/bin/python ]; then ln -sf python3 /usr/bin/python ; fi && \
    python3 -m ensurepip && \
    rm -r /usr/lib/python*/ensurepip && \
    pip3 install --no-cache --upgrade pip setuptools wheel && \
    if [ ! -e /usr/bin/pip ]; then ln -s pip3 /usr/bin/pip ; fi
RUN pip install twisted && \
    pip install pyOpenSSL
ADD bitcoinDEws.py /app/
WORKDIR /app
CMD [ "python", "./bitcoinDEws.py"]