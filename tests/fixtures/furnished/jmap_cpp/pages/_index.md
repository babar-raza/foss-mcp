---
layout: plugin
family_name: Aspose.JMAP FOSS
plugin_description: 'Aspose.JMAP FOSS for C++ is a free, open source, header-only JMAP client library for C++20 — for talking to a JMAP mail server over HTTP: RFC 8620 Core (session, Core/echo, blob upload/download, batched method calls) and RFC 8621 Mail (Mailbox/Email/Thread/Identity/SearchSnippet) plus EmailSubmission. Its public API is styled after Aspose.Email''s client conventions — a client object plus an options struct, a connect() call that returns the session, and strongly-typed message and folder models. The only bundled dependency is nlohmann::json, fetched automatically by CMake.'
plugin_platform: C++
head_title: Aspose.JMAP FOSS for C++ | Open-Source Library
head_description: 'Aspose.JMAP FOSS for C++ is a free, open source, header-only JMAP client library for C++20 — for talking to a JMAP mail server over HTTP: RFC 8620 Core...'
title: Aspose.JMAP FOSS for C++
description: 'Aspose.JMAP FOSS for C++ is a free, open source, header-only JMAP client library for C++20 — for talking to a JMAP mail server over HTTP: RFC 8620 Core (session, Core/echo, blob upload/download, batched method calls) and RFC 8621 Mail (Mailbox/Email/Thread/Identity/SearchSnippet) plus EmailSubmission. Its public API is styled after Aspose.Email''s client conventions — a client object plus an options struct, a connect() call that returns the session, and strongly-typed message and folder models. The only bundled dependency is nlohmann::json, fetched automatically by CMake.'
submenu:
  enable: true
github_url: https://github.com/aspose-email-foss/Aspose.JMAP-FOSS-for-Cpp
doc_source: github_readme
doc_source_commit: 559c962081323468039dc75cf38676499f32b20e
overview:
  enable: true
  title: Aspose.JMAP FOSS for C++
  content: |-
    ```mermaid
    flowchart TD
      subgraph StartingPoints["Starting Points"]
        direction TB
        i1["A JMAP session URL (.well-known/jmap)"]
        i2["Credentials: username/password or an OAuth 2.0 bearer token"]
      end
      PRODUCT["Aspose.JMAP FOSS for C++ (aspose_jmap::JmapClient)"]
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
        o2["Raw JMAP Request/Response (nlohmann::json) for advanced use"]
      end
      StartingPoints --> PRODUCT --> Capabilities --> Outputs
    ```
content:
  enable: true
  block:
  - title_left: Installation, Dependencies, and Quick Start
    content_left: |-
      ### Installation

      No package has been published to a registry. Consume the library from source with CMake — add it
      as a subdirectory of your own build, or use `FetchContent`:

      ```cmake
      add_subdirectory(Aspose.JMAP-FOSS-for-Cpp)
      target_link_libraries(your_app PRIVATE aspose-jmap-foss-cpp)
      ```

      Because the library is header-only, you can also just add `include/` to your include path and
      `#include "JmapClient.hpp"` directly, as long as `nlohmann/json.hpp` is discoverable.

      ### Required Package Dependencies

      `nlohmann::json` only — fetched automatically by the project's CMake (`FetchContent`). No other
      third-party library is required to build, use, or test the code.

      ### Native and System Requirements

      - A C++20 compiler.
      - CMake 3.20 or later.

      ### Development Dependencies

      - None. Tests are a plain CTest-registered executable built against the standard library — no
        external test framework is fetched.

      ### Quick Start

      ```cpp
      #include "JmapClient.hpp"

      aspose_jmap::JmapClientOptions options;
      options.sessionUrl = "https://jmap.example.test/.well-known/jmap";
      options.username = "user@example.test";
      options.password = "secret";
      aspose_jmap::JmapClient client(options);
      auto session = client.connect();
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
      - **Pluggable transport** — the client takes a `Transport` interface; every unit test supplies a
        hand-written fake, so no test needs a network.
      - **OAuth 2.0** — a `bearerToken` option ([RFC 6750](https://www.rfc-editor.org/rfc/rfc6750)) as
        an alternative to HTTP Basic.

      ### API Reference

      `aspose_jmap::JmapClient` (with `JmapClientOptions`) is the single entry point; the model types
      `Session`, `Mailbox`, `Email`, `EmailAddress`, `Thread`, `Identity`, `EmailSubmission`, and
      `SearchSnippet` mirror the JMAP objects one-to-one, and `Invocation` / `ResultReference` model
      raw method calls for `sendRequest`. Errors surface as `JmapNetworkException` (transport) and
      `JmapProtocolException` (a JMAP method-level error); per-item `Set` failures are returned as
      data on the result rather than thrown.

      The protocol/API reference is generated from the same specifications that drive this library.
  - title_left: Scope and Limitations
    content_left: |-
      Known issues, unsupported functionality, and other troubleshooting-relevant scope boundaries for Aspose.JMAP FOSS for C++, straight from its README at the pinned commit:

      - **Protocol**: JMAP Core (RFC 8620) and JMAP Mail (RFC 8621: Mailbox/Email/Thread/Identity/SearchSnippet)
        plus EmailSubmission.
      - **Out of scope for v1**:
        - Push / `EventSource` streaming — the type exists but is a stub/no-op.
        - JMAP for Calendars and Contacts.
        - `Date`/`UTCDate` values are kept as raw RFC 3339 strings (no time-point parsing) to avoid
          timezone-conversion bugs.
      - **Platform note**: the default `Transport` implementation (`WinHttpTransport`) uses the native
        Windows WinHTTP API and is Windows-only for now. The `Transport` interface itself is portable;
        any platform can supply its own subclass via `JmapClientOptions::transport`.
      - Unit tests run against a hand-written fake transport with mocked responses — no live JMAP
        server is required. A Docker-based live-server integration suite (Stalwart Mail Server) lives
        in a separate Docker-based live-server suite used during release validation.
single:
  enable: true
  block:
  - title: Quick Start
    content: |
      ```cpp
      #include "JmapClient.hpp"

      aspose_jmap::JmapClientOptions options;
      options.sessionUrl = "https://jmap.example.test/.well-known/jmap";
      options.username = "user@example.test";
      options.password = "secret";
      aspose_jmap::JmapClient client(options);
      auto session = client.connect();
      ```
  - title: OAuth 2.0 bearer token authentication
    content: |
      Authenticate with an OAuth 2.0 bearer token
      ([RFC 6750](https://www.rfc-editor.org/rfc/rfc6750)) instead of username/password — when
      `bearerToken` is set to a non-empty value it takes precedence:

      ```cpp
      aspose_jmap::JmapClientOptions options;
      options.sessionUrl = "https://jmap.example.test/.well-known/jmap";
      options.bearerToken = "eyJhbGciOi..."; // OAuth 2.0 access token
      aspose_jmap::JmapClient client(options);
      auto session = client.connect();
      ```
  - title: OAuth 2.0 bearer token authentication (2)
    content: |
      Multiple method calls can be batched into a single HTTP round trip via `sendRequest`, using a
      `ResultReference` ([RFC 8620 §3.7](https://www.rfc-editor.org/rfc/rfc8620#section-3.7)) to chain
      a later call to an earlier one's result without a second request:

      ```cpp
      aspose_jmap::Invocation query{"Email/query", {{"accountId", accountId}}, "c1"};

      aspose_jmap::ResultReference ref{"c1", "Email/query", "/ids"};
      aspose_jmap::Invocation get{"Email/get", {{"accountId", accountId}, {"#ids", ref.toJson()}}, "c2"};

      nlohmann::json resp = client.sendRequest({query, get}, {"urn:ietf:params:jmap:mail"});
      ```
faq:
  enable: false
  list: []
---
