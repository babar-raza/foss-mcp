---
layout: plugin
family_name: Aspose.JMAP FOSS
plugin_description: 'Aspose.JMAP FOSS for Java is a free, open source JMAP client library for Java 17 — for talking to a JMAP mail server over HTTP: RFC 8620 Core (session, Core/echo, blob upload/download, batched method calls) and RFC 8621 Mail (Mailbox/Email/Thread/Identity/SearchSnippet) plus EmailSubmission. Its public API is styled after Aspose.Email''s client conventions — a client object plus a builder-style options object, a connect() call that returns the session, and strongly-typed message and folder models.'
plugin_platform: Java
head_title: Aspose.JMAP FOSS for Java | Open-Source Library
head_description: 'Aspose.JMAP FOSS for Java is a free, open source JMAP client library for Java 17 — for talking to a JMAP mail server over HTTP: RFC 8620 Core (session...'
title: Aspose.JMAP FOSS for Java
description: 'Aspose.JMAP FOSS for Java is a free, open source JMAP client library for Java 17 — for talking to a JMAP mail server over HTTP: RFC 8620 Core (session, Core/echo, blob upload/download, batched method calls) and RFC 8621 Mail (Mailbox/Email/Thread/Identity/SearchSnippet) plus EmailSubmission. Its public API is styled after Aspose.Email''s client conventions — a client object plus a builder-style options object, a connect() call that returns the session, and strongly-typed message and folder models.'
submenu:
  enable: true
github_url: https://github.com/aspose-email-foss/Aspose.JMAP-FOSS-for-Java
doc_source: github_readme
doc_source_commit: f0d6ba73dc92db4b3effaa7c1fa76682e2d09fd4
overview:
  enable: true
  title: Aspose.JMAP FOSS for Java
  content: |-
    ```mermaid
    flowchart TD
      subgraph StartingPoints["Starting Points"]
        direction TB
        i1["A JMAP session URL (.well-known/jmap)"]
        i2["Credentials: username/password or an OAuth 2.0 bearer token"]
      end
      PRODUCT["Aspose.JMAP FOSS for Java (JmapClient)"]
      subgraph Capabilities["Core Capabilities"]
        direction TB
        c1["Connect and read the JMAP Session (accounts, capabilities)"]
        c2["Mailbox CRUD, Email query/get/set, Thread and Identity reads"]
        c3["EmailSubmission: send, cancel, list"]
        c4["Blob upload/download; batched calls with ResultReference"]
      end
      subgraph Outputs["Outputs"]
        direction TB
        o1["Strongly-typed models (Mailbox, Email, Thread, ...)"]
        o2["Raw JMAP Request/Response for advanced use"]
      end
      StartingPoints --> PRODUCT --> Capabilities --> Outputs
    ```
content:
  enable: true
  block:
  - title_left: Installation, Dependencies, and Quick Start
    content_left: |-
      ### Installation

      No artifact has been published to Maven Central yet; until it is, build and install the library
      locally (see [Development and Testing](#development-and-testing)). The intended coordinates are
      `com.aspose:aspose-jmap-foss`:

      ```xml
      <dependency>
        <groupId>com.aspose</groupId>
        <artifactId>aspose-jmap-foss</artifactId>
        <version>0.1.0</version>
      </dependency>
      ```

      ### Required Package Dependencies

      JSON handling uses a small vendored/generated model layer plus the Java standard library; see
      `pom.xml` for the exact `<dependencies>` the generated code compiles against.

      ### Native and System Requirements

      - Java 17 or later. **No local JDK or Maven is required** — see
        [Development and Testing](#development-and-testing): the build runs inside a throwaway
        `maven:3.9-eclipse-temurin-17` Docker container, so only Docker is needed.

      ### Development Dependencies

      - JUnit — the unit-test framework, declared test-scope in `pom.xml`.

      ### Quick Start

      ```java
      import com.aspose.jmap.JmapClient;
      import com.aspose.jmap.JmapClientOptions;
      import com.aspose.jmap.Session;

      JmapClientOptions options = new JmapClientOptions.Builder()
          .sessionUrl("https://jmap.example.test/.well-known/jmap")
          .username("user@example.test")
          .password("secret")
          .build();
      JmapClient client = new JmapClient(options);
      Session session = client.connect();
      ```
    title_right: Key Capabilities and API Reference
    content_right: |-
      ### Key Capabilities

      - **Connect and inspect the session** — `client.connect()` fetches `/.well-known/jmap` and
        returns the `Session` (account ids, `capabilities`, `apiUrl`, `uploadUrl`, `downloadUrl`).
      - **Mailboxes** — `listMailboxes()`, `getMailbox()`, `createMailbox()`, `deleteMailbox()` wrap
        `Mailbox/get`, `Mailbox/query`, and `Mailbox/set`.
      - **Messages** — `listMessages()`, `fetchMessage()`, `moveMessage()`, `setMessageKeyword()`,
        `deleteMessage()` over `Email/query`, `Email/get`, and `Email/set`.
      - **Identities** — `listIdentities()` over `Identity/get`.
      - **Sending** — `send()`, `cancelSend()`, `listSubmissions()` wrap `EmailSubmission/set` and
        `EmailSubmission/get`.
      - **Blobs** — `uploadBlob()` / `downloadBlob()` for `/upload` and `/download`.
      - **Batching** — `sendRequest()` sends any list of `Invocation`s in one HTTP round trip, with
        `ResultReference` ([RFC 8620 §3.7](https://www.rfc-editor.org/rfc/rfc8620#section-3.7)) to
        chain one call's result into the next.
      - **Pluggable transport** — the client takes a `JmapTransport`; every unit test supplies a fake,
        so no test needs a network.
      - **OAuth 2.0** — `JmapClientOptions.Builder.bearerToken(...)`
        ([RFC 6750](https://www.rfc-editor.org/rfc/rfc6750)) as an alternative to HTTP Basic.

      ### API Reference

      `JmapClient` (with `JmapClientOptions`) is the single entry point; the model classes `Session`,
      `Mailbox`, `Email`, `EmailAddress`, `Thread`, `Identity`, `EmailSubmission`, and `SearchSnippet`
      mirror the JMAP objects one-to-one, and `Invocation` / `ResultReference` model raw method calls
      for `sendRequest`. Errors surface as `JmapNetworkException` (transport) and
      `JmapProtocolException` (a JMAP method-level error); per-item `Set` failures are returned as
      data on the result rather than thrown.

      The protocol/API reference is generated from the same specifications that drive this library.
  - title_left: Scope and Limitations
    content_left: |-
      Known issues, unsupported functionality, and other troubleshooting-relevant scope boundaries for Aspose.JMAP FOSS for Java, straight from its README at the pinned commit:

      - **Protocol**: JMAP Core (RFC 8620) and JMAP Mail (RFC 8621: Mailbox/Email/Thread/Identity/SearchSnippet)
        plus EmailSubmission.
      - **Out of scope for v1**:
        - Push / `EventSource` streaming — the type exists but is a stub/no-op.
        - JMAP for Calendars and Contacts.
        - `Date`/`UTCDate` values are kept as raw RFC 3339 strings (no `java.time` parsing) to avoid
          timezone-conversion bugs.
      - Unit tests run against a fake `JmapTransport` with mocked responses — no live JMAP server is
        required. A Docker-based live-server integration suite (Stalwart Mail Server) lives in
        a separate Docker-based live-server suite used during release validation.
single:
  enable: true
  block:
  - title: Quick Start
    content: |
      ```java
      import com.aspose.jmap.JmapClient;
      import com.aspose.jmap.JmapClientOptions;
      import com.aspose.jmap.Session;

      JmapClientOptions options = new JmapClientOptions.Builder()
          .sessionUrl("https://jmap.example.test/.well-known/jmap")
          .username("user@example.test")
          .password("secret")
          .build();
      JmapClient client = new JmapClient(options);
      Session session = client.connect();
      ```
  - title: OAuth 2.0 bearer token authentication
    content: |
      Authenticate with an OAuth 2.0 bearer token
      ([RFC 6750](https://www.rfc-editor.org/rfc/rfc6750)) instead of username/password — when
      `.bearerToken(...)` is set to a non-empty value it takes precedence and the client sends
      `Authorization: Bearer <token>`:

      ```java
      JmapClientOptions options = new JmapClientOptions.Builder()
          .sessionUrl("https://jmap.example.test/.well-known/jmap")
          .bearerToken("eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...")
          .build();
      JmapClient client = new JmapClient(options);
      Session session = client.connect();
      ```
  - title: OAuth 2.0 bearer token authentication (2)
    content: |
      Multiple method calls can be batched into a single HTTP round trip via `sendRequest`, using a
      `ResultReference` ([RFC 8620 §3.7](https://www.rfc-editor.org/rfc/rfc8620#section-3.7)) to chain
      a later call to an earlier one's result without a second request:

      ```java
      Invocation query = new Invocation("Email/query", Map.of("accountId", accountId), "c1");
      Invocation get = new Invocation(
              "Email/get",
              Map.of(
                      "accountId", accountId,
                      "#ids", new ResultReference("c1", "Email/query", "/ids")),
              "c2");
      JmapResponseEnvelope resp = client.sendRequest(List.of(query, get), List.of("urn:ietf:params:jmap:mail"));
      ```
faq:
  enable: false
  list: []
---
