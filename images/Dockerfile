FROM --platform=linux/amd64 hub.opensciencegrid.org/pelican_platform/origin:v7.6.5

ARG TARGETARCH

RUN dnf install -y python39 python39-devel python39-pip gcc

RUN alternatives --set python3 /usr/bin/python3.9

COPY requirements.txt requirements.txt

RUN pip3.9 install -r requirements.txt

RUN pip3.9 install pytest