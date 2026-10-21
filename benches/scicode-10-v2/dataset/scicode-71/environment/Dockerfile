# Frozen: the base image digest and the package versions below pin the numeric environment the verifier
# compares against test_data.h5. Changing either changes the task content digest.
FROM python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce

RUN apt-get update && apt-get install -y \
    build-essential curl git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install scientific computing dependencies (versions as installed on 2026-10-20)
RUN pip install --no-cache-dir \
    numpy==2.4.6 scipy==1.17.1 matplotlib==3.11.2 sympy==1.14.0 h5py==3.16.0


ENV PYTHONPATH=/app:/tests
ENV TEST_DIR=/tests
