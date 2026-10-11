---
layout: plugin
family_name: Aspose.JMAP FOSS
plugin_description: 'Aspose.JMAP FOSS for Go is a free, open source JMAP client library for Go — a package for talking to a JMAP mail server over HTTP: RFC 8620 Core (session, Core/echo, blob upload/download, batched method calls) and RFC 8621 Mail (Mailbox/Email/Thread/Identity/SearchSnippet) plus EmailSubmission. Its public API is styled after Aspose.Email''s client conventions — a client value plus an options struct, a Connect call that returns the session, and strongly-typed message and folder models — and it depends only on the Go standard library.'
plugin_platform: Go
head_title: Aspose.JMAP FOSS for Go | Open-Source Library
head_description: 'Aspose.JMAP FOSS for Go is a free, open source JMAP client library for Go — a package for talking to a JMAP mail server over HTTP: RFC 8620 Core (session...'
title: Aspose.JMAP FOSS for Go
description: 'Aspose.JMAP FOSS for Go is a free, open source JMAP client library for Go — a package for talking to a JMAP mail server over HTTP: RFC 8620 Core (session, Core/echo, blob upload/download, batched method calls) and RFC 8621 Mail (Mailbox/Email/Thread/Identity/SearchSnippet) plus EmailSubmission. Its public API is styled after Aspose.Email''s client conventions — a client value plus an options struct, a Connect call that returns the session, and strongly-typed message and folder models — and it depends only on the Go standard library.'
submenu:
  enable: true
github_url: https://github.com/aspose-email-foss/Aspose.JMAP-FOSS-for-Go
doc_source: github_readme
doc_source_commit: 327dda39116ba02657fa49ec6dbeb69965628447
overview:
  enable: true
  title: Aspose.JMAP FOSS for Go
  content: |-
    ```mermaid
    flowchart TD
      subgraph StartingPoints["Starting Points"]
        direction TB
        i1["A JMAP session URL (.well-known/jmap)"]
        i2["Credentials: username/password or an OAuth 2.0 bearer token"]
      end
      PRODUCT["Aspose.JMAP FOSS for Go (jmap.JmapClient)"]
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

      The module path is `github.com/aspose-email-foss/Aspose.JMAP-FOSS-for-Go` (imported as package `jmap`). No versioned tag
      has been published yet; until one is, vendor the repository or point a `replace` directive at a
      local checkout (see [Development and Testing](#development-and-testing)).

      ```bash
      go get github.com/aspose-email-foss/Aspose.JMAP-FOSS-for-Go
      ```

      ### Required Package Dependencies

      None. `go.mod` declares no `require`d modules — JSON and HTTP come from the standard library.

      ### Native and System Requirements

      - Go 1.21 or later.

      ### Development Dependencies

      - None beyond the Go toolchain; tests use the standard `testing` package.

      ### Quick Start

      ```go
      import "github.com/aspose-email-foss/Aspose.JMAP-FOSS-for-Go"

      options := jmap.ClientOptions{
          SessionURL: "https://jmap.example.test/.well-known/jmap",
          Username:   "user@example.test",
          Password:   "secret",
      }
      client := jmap.NewClient(options)
      session, err := client.Connect()
      ```
    title_right: Key Capabilities and API Reference
    content_right: |-
      ### Key Capabilities

      - **Connect and inspect the session** — `client.Connect()` fetches `/.well-known/jmap` and
        returns the `Session` (account ids, `Capabilities`, `ApiURL`, `UploadURL`, `DownloadURL`).
      - **Mailboxes** — `ListMailboxes()`, `GetMailbox()`, `CreateMailbox()`, `DeleteMailbox()` wrap
        `Mailbox/get`, `Mailbox/query`, and `Mailbox/set`.
      - **Messages** — `ListMessages()`, `FetchMessage()`, `MoveMessage()`, `SetMessageKeyword()`,
        `DeleteMessage()` over `Email/query`, `Email/get`, and `Email/set`.
      - **Identities** — `ListIdentities()` over `Identity/get`.
      - **Sending** — `Send()`, `CancelSend()`, `ListSubmissions()` wrap `EmailSubmission/set` and
        `EmailSubmission/get`.
      - **Blobs** — `UploadBlob()` / `DownloadBlob()` for `/upload` and `/download`.
      - **Batching** — `SendRequest()` sends any slice of `Invocation`s in one HTTP round trip, with
        `ResultReference` ([RFC 8620 §3.7](https://www.rfc-editor.org/rfc/rfc8620#section-3.7)) to
        chain one call's result into the next.
      - **Pluggable transport** — the client takes a `Transport` interface; every unit test supplies a
        fake, so no test needs a network.
      - **OAuth 2.0** — a `BearerToken` option ([RFC 6750](https://www.rfc-editor.org/rfc/rfc6750)) as
        an alternative to HTTP Basic.

      ### API Reference

      `jmap.NewClient` / `jmap.ClientOptions` is the single entry point; the model types `Session`,
      `Mailbox`, `Email`, `EmailAddress`, `Thread`, `Identity`, `EmailSubmission`, and `SearchSnippet`
      mirror the JMAP objects one-to-one, and `Invocation` / `ResultReference` model raw method calls
      for `SendRequest`. Errors surface as `JmapNetworkError` (transport) and `JmapProtocolError` (a
      JMAP method-level error); per-item `Set` failures are returned as data on the result value
      rather than as an `error`.

      The protocol/API reference is generated from the same specifications that drive this library.
  - title_left: Scope and Limitations
    content_left: |-
      Known issues, unsupported functionality, and other troubleshooting-relevant scope boundaries for Aspose.JMAP FOSS for Go, straight from its README at the pinned commit:

      - **Protocol**: JMAP Core (RFC 8620) and JMAP Mail (RFC 8621: Mailbox/Email/Thread/Identity/SearchSnippet)
        plus EmailSubmission.
      - **Out of scope for v1**:
        - Push / `EventSource` streaming — the type exists but is a stub/no-op.
        - JMAP for Calendars and Contacts.
        - `Date`/`UTCDate` values are kept as raw RFC 3339 strings (no `time.Time` parsing) to avoid
          timezone-conversion bugs.
      - Unit tests run against a fake `Transport` with mocked responses — no live JMAP server is
        required. A Docker-based live-server integration suite (Stalwart Mail Server) lives in
        a separate Docker-based live-server suite used during release validation.
single:
  enable: true
  block:
  - title: Quick Start
    content: |
      ```go
      import "github.com/aspose-email-foss/Aspose.JMAP-FOSS-for-Go"

      options := jmap.ClientOptions{
          SessionURL: "https://jmap.example.test/.well-known/jmap",
          Username:   "user@example.test",
          Password:   "secret",
      }
      client := jmap.NewClient(options)
      session, err := client.Connect()
      ```
  - title: OAuth 2.0 bearer token authentication
    content: |
      Authenticate with an OAuth 2.0 bearer token
      ([RFC 6750](https://www.rfc-editor.org/rfc/rfc6750)) by setting `BearerToken`; when non-empty it
      takes precedence over Basic authentication:

      ```go
      options := jmap.ClientOptions{
          SessionURL:  "https://jmap.example.test/.well-known/jmap",
          BearerToken: "oauth-access-token",
      }
      client := jmap.NewClient(options)
      session, err := client.Connect()
      ```
  - title: OAuth 2.0 bearer token authentication (2)
    content: |
      Multiple method calls can be batched into a single HTTP round trip via `SendRequest`, using a
      `ResultReference` ([RFC 8620 §3.7](https://www.rfc-editor.org/rfc/rfc8620#section-3.7)) to chain
      a later call to an earlier one's result without a second request:

      ```go
      query := jmap.Invocation{Name: "Email/query", Arguments: map[string]interface{}{"accountId": accountId}, MethodCallId: "c1"}
      get := jmap.Invocation{
          Name: "Email/get",
          Arguments: map[string]interface{}{
              "accountId": accountId,
              "#ids":      jmap.ResultReference{ResultOf: "c1", Name: "Email/query", Path: "/ids"},
          },
          MethodCallId: "c2",
      }
      resp, err := client.SendRequest([]jmap.Invocation{query, get}, []string{"urn:ietf:params:jmap:mail"})
      ```
faq:
  enable: false
  list: []
---
