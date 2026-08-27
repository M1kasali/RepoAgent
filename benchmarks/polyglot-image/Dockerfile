FROM eclipse-temurin:21-jdk-jammy@sha256:ce5767b7222312d42395f5bab033cd91f09e44032a2f21bdfd7b5b912dbe1e77

ARG GO_VERSION=1.21.5
ARG RUST_VERSION=1.83.0
ARG NODE_VERSION=20.18.1
ARG GRADLE_VERSION=8.7
ARG TARGETARCH

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        curl \
        libboost-date-time-dev \
        python3 \
        python3-pip \
        unzip \
    && python3 -m pip install --no-cache-dir pytest \
    && rm -rf /var/lib/apt/lists/*

RUN case "${TARGETARCH}" in \
        amd64) go_arch=amd64; node_arch=x64 ;; \
        arm64) go_arch=arm64; node_arch=arm64 ;; \
        *) echo "unsupported target architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && curl --fail --location --silent --show-error \
        "https://go.dev/dl/go${GO_VERSION}.linux-${go_arch}.tar.gz" \
        --output /tmp/go.tar.gz \
    && tar -C /usr/local -xzf /tmp/go.tar.gz \
    && curl --fail --location --silent --show-error \
        "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${node_arch}.tar.xz" \
        --output /tmp/node.tar.xz \
    && tar -C /opt -xJf /tmp/node.tar.xz \
    && ln -s "/opt/node-v${NODE_VERSION}-linux-${node_arch}" /opt/node \
    && rm /tmp/go.tar.gz /tmp/node.tar.xz

ENV RUSTUP_HOME=/opt/rustup \
    CARGO_HOME=/opt/cargo
RUN curl --proto '=https' --tlsv1.2 --fail --silent --show-error \
        https://sh.rustup.rs --output /tmp/rustup-init.sh \
    && sh /tmp/rustup-init.sh -y --no-modify-path --profile minimal \
        --default-toolchain "${RUST_VERSION}" \
    && rm /tmp/rustup-init.sh

RUN curl --fail --location --silent --show-error \
        "https://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip" \
        --output /tmp/gradle.zip \
    && unzip -q /tmp/gradle.zip -d /opt \
    && ln -s "/opt/gradle-${GRADLE_VERSION}" /opt/gradle \
    && rm /tmp/gradle.zip

ENV PATH=/usr/local/go/bin:/opt/cargo/bin:/opt/node/bin:/opt/gradle/bin:${PATH}

RUN mkdir -p /npm-install \
    && cd /npm-install \
    && npm init --yes >/dev/null \
    && npm install --ignore-scripts \
        @babel/core@7.25.2 \
        @exercism/babel-preset-javascript@0.2.1 \
        @exercism/eslint-config-javascript@0.6.0 \
        @types/jest@29.5.12 \
        @types/node@20.12.12 \
        babel-jest@29.6.4 \
        core-js@3.37.1 \
        eslint@8.49.0 \
        jest@29.7.0 \
    && npm cache clean --force

COPY benchmarks/polyglot-image/java-seed/ /tmp/java-seed/
RUN cd /tmp/java-seed \
    && GRADLE_USER_HOME=/opt/gradle-cache gradle test --no-daemon \
    && rm -rf /tmp/java-seed /opt/gradle-cache/caches/*/fileHashes \
        /opt/gradle-cache/caches/*/fileContent

COPY benchmarks/polyglot-image/cpp-test.sh /repoagent/benchmarks/cpp-test.sh
COPY benchmarks/polyglot-image/npm-test.sh /repoagent/benchmarks/npm-test.sh
COPY benchmarks/polyglot-image/entrypoint.sh /usr/local/bin/repoagent-benchmark-entrypoint
RUN chmod 0555 \
        /repoagent/benchmarks/cpp-test.sh \
        /repoagent/benchmarks/npm-test.sh \
        /usr/local/bin/repoagent-benchmark-entrypoint \
    && chmod -R a-w /repoagent /npm-install /opt/gradle-cache

LABEL org.opencontainers.image.title="RepoAgent Polyglot benchmark runtime" \
      org.opencontainers.image.description="Offline six-language grader for the frozen Aider Polyglot workload"

ENTRYPOINT ["/usr/local/bin/repoagent-benchmark-entrypoint"]
CMD ["sh"]
