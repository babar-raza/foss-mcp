---
layout: plugin
family_name: Aspose.JMAP FOSS
plugin_description: 'Aspose.JMAP FOSS for Node.js is a free, open source JMAP client library for Node.js — an ES-module toolkit for talking to a JMAP mail server over HTTP: RFC 8620 Core (session, Core/echo, blob upload/download, batched method calls) and RFC 8621 Mail (Mailbox/Email/Thread/Identity/SearchSnippet) plus EmailSubmission. Its public API is styled after Aspose.Email''s client conventions — a client object plus an options object, connect()/close() lifecycle, and strongly-typed message and folder models — and it ships with no runtime dependencies (it uses the built-in fetch).'
plugin_platform: Node.js
head_title: Aspose.JMAP FOSS for Node.js | Open-Source Library
head_description: 'Aspose.JMAP FOSS for Node.js is a free, open source JMAP client library for Node.js — an ES-module toolkit for talking to a JMAP mail server over HTTP: RFC...'
title: Aspose.JMAP FOSS for Node.js
description: 'Aspose.JMAP FOSS for Node.js is a free, open source JMAP client library for Node.js — an ES-module toolkit for talking to a JMAP mail server over HTTP: RFC 8620 Core (session, Core/echo, blob upload/download, batched method calls) and RFC 8621 Mail (Mailbox/Email/Thread/Identity/SearchSnippet) plus EmailSubmission. Its public API is styled after Aspose.Email''s client conventions — a client object plus an options object, connect()/close() lifecycle, and strongly-typed message and folder models — and it ships with no runtime dependencies (it uses the built-in fetch).'
submenu:
  enable: true
github_url: https://github.com/aspose-email-foss/Aspose.JMAP-FOSS-for-Node.js
doc_source: github_readme
doc_source_commit: 93f22fb57ba83484d14af5af2017590a0f5bf55f
overview:
  enable: true
  title: Aspose.JMAP FOSS for Node.js
  content: |-
    ```mermaid
    flowchart TD
      subgraph StartingPoints["Starting Points"]
        direction TB
        i1["A JMAP session URL (.well-known/jmap)"]
        i2["Credentials: username/password or an OAuth 2.0 bearer token"]
      end
      PRODUCT["Aspose.JMAP FOSS for Node.js (JmapClient)"]
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

      No package has been published to npm yet; until it is, build the library from source (see
      [Development and Testing](#development-and-testing)). The intended published id is
      `aspose-jmap-foss`:

      ```bash
      npm install aspose-jmap-foss
      ```

      The package is an ES module (`"type": "module"`) and targets Node.js 18 or later (for the
      built-in `fetch`).

      ### Required Package Dependencies

      None. The client uses the global `fetch` from Node.js 18+; there are no `dependencies` in
      `package.json`.

      ### Native and System Requirements

      - Node.js 18.0.0 or later.

      ### Development Dependencies

      - The test suite runs on the built-in `node --test` runner — no test framework to install.

      ### Quick Start

      ```javascript
      import { JmapClient } from "aspose-jmap-foss";

      const client = new JmapClient({
        sessionUrl: "https://jmap.example.test/.well-known/jmap",
        username: "user@example.test",
        password: "secret",
      });
      await client.connect();
      const mailboxes = await client.listMailboxes();
      await client.close();
      ```
    title_right: Key Capabilities and API Reference
    content_right: |-
      ### Key Capabilities

      - **Connect and inspect the session** — `client.connect()` fetches `/.well-known/jmap` and
        exposes the session (account ids, `capabilities`, `apiUrl`, `uploadUrl`, `downloadUrl`).
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
      - **Pluggable transport** — a stub `fetchImpl` can be injected, so no unit test needs a network.
      - **OAuth 2.0** — a `bearerToken` option ([RFC 6750](https://www.rfc-editor.org/rfc/rfc6750)) as
        an alternative to HTTP Basic.

      ### API Reference

      `JmapClient` is the single entry point; the model classes `Session`, `Mailbox`, `Email`,
      `EmailAddress`, `Thread`, `Identity`, `EmailSubmission`, and `SearchSnippet` mirror the JMAP
      objects one-to-one, and `Invocation` / `ResultReference` model raw method calls for
      `sendRequest()`. Errors surface as `JmapNetworkError` (transport) and `JmapProtocolError` (a
      JMAP method-level error); per-item `Set` failures are returned as data on the result object
      rather than thrown.

      The protocol/API reference is generated from the same specifications that drive this library.
  - title_left: Scope and Limitations
    content_left: |-
      Known issues, unsupported functionality, and other troubleshooting-relevant scope boundaries for Aspose.JMAP FOSS for Node.js, straight from its README at the pinned commit:

      - **Protocol**: JMAP Core (RFC 8620) and JMAP Mail (RFC 8621: Mailbox/Email/Thread/Identity/SearchSnippet)
        plus EmailSubmission.
      - **Out of scope for v1**:
        - Push / `EventSource` streaming — the type exists but is a stub/no-op.
        - JMAP for Calendars and Contacts.
        - `Date`/`UTCDate` values are kept as raw RFC 3339 strings (no `Date` parsing) to avoid
          timezone-conversion bugs.
      - Unit tests run against a stub `fetchImpl` with mocked responses — no live JMAP server is
        required. A Docker-based live-server integration suite (Stalwart Mail Server) lives in
        a separate Docker-based live-server suite used during release validation.
single:
  enable: true
  block:
  - title: Quick Start
    content: |
      ```javascript
      import { JmapClient } from "aspose-jmap-foss";

      const client = new JmapClient({
        sessionUrl: "https://jmap.example.test/.well-known/jmap",
        username: "user@example.test",
        password: "secret",
      });
      await client.connect();
      const mailboxes = await client.listMailboxes();
      await client.close();
      ```
  - title: OAuth 2.0 bearer token authentication
    content: |
      Authenticate with an OAuth 2.0 bearer token
      ([RFC 6750](https://www.rfc-editor.org/rfc/rfc6750)) instead of username/password; when set, it
      takes precedence and the client sends `Authorization: Bearer <token>`:

      ```javascript
      const client = new JmapClient({
        sessionUrl: "https://jmap.example.test/.well-known/jmap",
        bearerToken: "your-oauth-access-token",
      });
      ```
  - title: OAuth 2.0 bearer token authentication (2)
    content: |
      Multiple method calls can be batched into a single HTTP round trip via `sendRequest()`, using a
      `ResultReference` ([RFC 8620 §3.7](https://www.rfc-editor.org/rfc/rfc8620#section-3.7)) to chain
      a later call to an earlier one's result without a second request:

      ```javascript
      import { Invocation, ResultReference } from "aspose-jmap-foss";

      const query = new Invocation("Email/query", { accountId }, "c1");
      const get = new Invocation(
        "Email/get",
        { accountId, "#ids": new ResultReference("c1", "Email/query", "/ids").toJson() },
        "c2",
      );
      const resp = await client.sendRequest([query, get], ["urn:ietf:params:jmap:mail"]);
      ```
faq:
  enable: false
  list: []
---
