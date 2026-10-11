---
layout: plugin
family_name: Aspose.JMAP FOSS
plugin_description: 'Aspose.JMAP FOSS for .NET is a free, open source JMAP client library for .NET — a C# toolkit for talking to a JMAP mail server over HTTP: RFC 8620 Core (session, Core/echo, blob upload/download, batched method calls) and RFC 8621 Mail (Mailbox/Email/Thread/Identity/SearchSnippet) plus EmailSubmission. Its public API is styled after Aspose.Email''s client conventions — a client object plus an options object, using-scoped disposal, and strongly-typed message and folder models — and it ships with no required third-party package dependencies.'
plugin_platform: .NET
head_title: Aspose.JMAP FOSS for .NET | Open-Source Library
head_description: 'Aspose.JMAP FOSS for .NET is a free, open source JMAP client library for .NET — a C# toolkit for talking to a JMAP mail server over HTTP: RFC 8620 Core...'
title: Aspose.JMAP FOSS for .NET
description: 'Aspose.JMAP FOSS for .NET is a free, open source JMAP client library for .NET — a C# toolkit for talking to a JMAP mail server over HTTP: RFC 8620 Core (session, Core/echo, blob upload/download, batched method calls) and RFC 8621 Mail (Mailbox/Email/Thread/Identity/SearchSnippet) plus EmailSubmission. Its public API is styled after Aspose.Email''s client conventions — a client object plus an options object, using-scoped disposal, and strongly-typed message and folder models — and it ships with no required third-party package dependencies.'
submenu:
  enable: true
github_url: https://github.com/aspose-email-foss/Aspose.JMAP-FOSS-for-.NET
doc_source: github_readme
doc_source_commit: 0d5cf17e0e9ee185eea2847f6587b918ac0eb016
overview:
  enable: true
  title: Aspose.JMAP FOSS for .NET
  content: |-
    ```mermaid
    flowchart TD
      subgraph StartingPoints["Starting Points"]
        direction TB
        i1["A JMAP session URL (.well-known/jmap)"]
        i2["Credentials: username/password or an OAuth 2.0 bearer token"]
      end
      PRODUCT["Aspose.JMAP FOSS for .NET (JmapClient)"]
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

      No package has been published to NuGet yet; until it is, build the library from source (see
      [Development and Testing](#development-and-testing)). The intended published id is
      `Aspose.JMAP.FOSS`:

      ```bash
      dotnet add package Aspose.JMAP.FOSS
      ```

      Or add it directly to a project file:

      ```xml
      <PackageReference Include="Aspose.JMAP.FOSS" Version="0.1.0" />
      ```

      The library targets .NET 8.0 and runs on Windows, Linux, and macOS.

      ### Required Package Dependencies

      None. JSON handling uses `System.Text.Json` from the base-class library; there are zero
      `<PackageReference>` entries in the library project.

      ### Native and System Requirements

      - The .NET 8.0 SDK (or later) to build, and the matching .NET 8.0 runtime (or later) to run.

      ### Development Dependencies

      - `Microsoft.NET.Test.Sdk`, `xunit`, and `xunit.runner.visualstudio` — the xUnit test host and
        adapter, used only by `tests/Aspose.Jmap.Tests`. They are never referenced by the library
        project and are not shipped with the package.

      ### Quick Start

      ```csharp
      using Aspose.Jmap;

      var options = new JmapClientOptions
      {
          SessionUrl = new Uri("https://jmap.example.test/.well-known/jmap"),
          Username = "user@example.test",
          Password = "secret",
      };

      using var client = new JmapClient(options);
      await client.ConnectAsync();

      IReadOnlyList<Mailbox> mailboxes = await client.ListMailboxesAsync();
      ```
    title_right: Key Capabilities and API Reference
    content_right: |-
      ### Key Capabilities

      - **Connect and inspect the session** — `JmapClient.ConnectAsync()` fetches
        `/.well-known/jmap` and exposes the `Session` (account ids, `capabilities`, `apiUrl`,
        `uploadUrl`, `downloadUrl`).
      - **Mailboxes** — `ListMailboxesAsync()`, `GetMailboxAsync()`, `CreateMailboxAsync()`,
        `DeleteMailboxAsync()` wrap `Mailbox/get`, `Mailbox/query`, and `Mailbox/set`.
      - **Messages** — `ListMessagesAsync()`, `FetchMessageAsync()`, `MoveMessageAsync()`,
        `SetMessageKeywordAsync()`, `DeleteMessageAsync()` over `Email/query`, `Email/get`, and
        `Email/set`.
      - **Identities** — `ListIdentitiesAsync()` over `Identity/get`.
      - **Sending** — `SendAsync()`, `CancelSendAsync()`, `ListSubmissionsAsync()` wrap
        `EmailSubmission/set` and `EmailSubmission/get`.
      - **Blobs** — `UploadBlobAsync()` / `DownloadBlobAsync()` for `/upload` and `/download`.
      - **Batching** — `SendRequestAsync()` sends any list of `Invocation`s in one HTTP round trip,
        with `ResultReference` ([RFC 8620 §3.7](https://www.rfc-editor.org/rfc/rfc8620#section-3.7))
        to chain one call's result into the next.
      - **Pluggable transport** — the client depends on `IJmapTransport`, not `HttpClient` directly;
        every unit test injects a fake transport, so no test needs a network.
      - **OAuth 2.0** — `JmapClientOptions.BearerToken` ([RFC 6750](https://www.rfc-editor.org/rfc/rfc6750))
        as an alternative to HTTP Basic.

      ### API Reference

      `JmapClient` (with `JmapClientOptions`) is the single entry point; the model classes
      `Session`, `Mailbox`, `Email`, `EmailAddress`, `Thread`, `Identity`, `EmailSubmission`, and
      `SearchSnippet` mirror the JMAP objects one-to-one, and `Invocation` / `ResultReference` model
      raw method calls for `SendRequestAsync`. Errors surface as `JmapNetworkException` (transport)
      and `JmapProtocolException` (a JMAP method-level error); per-item `Set` failures are returned as
      data on the result object rather than thrown.

      The protocol/API reference is generated from the same specifications that drive this library.
  - title_left: Scope and Limitations
    content_left: |-
      Known issues, unsupported functionality, and other troubleshooting-relevant scope boundaries for Aspose.JMAP FOSS for .NET, straight from its README at the pinned commit:

      - **Protocol**: JMAP Core (RFC 8620) and JMAP Mail (RFC 8621: Mailbox/Email/Thread/Identity/SearchSnippet)
        plus EmailSubmission.
      - **Out of scope for v1**:
        - Push / `EventSource` streaming — the type exists but is a stub/no-op.
        - JMAP for Calendars and Contacts.
        - `Date`/`UTCDate` values are kept as raw RFC 3339 strings (no `DateTimeOffset` parsing) to
          avoid timezone-conversion bugs.
      - Unit tests run against an injectable transport with mocked responses — no live JMAP server is
        required. A Docker-based live-server integration suite (Stalwart Mail Server) lives in
        a separate Docker-based live-server suite used during release validation.
single:
  enable: true
  block:
  - title: Quick Start
    content: |
      ```csharp
      using Aspose.Jmap;

      var options = new JmapClientOptions
      {
          SessionUrl = new Uri("https://jmap.example.test/.well-known/jmap"),
          Username = "user@example.test",
          Password = "secret",
      };

      using var client = new JmapClient(options);
      await client.ConnectAsync();

      IReadOnlyList<Mailbox> mailboxes = await client.ListMailboxesAsync();
      ```
  - title: OAuth 2.0 bearer token authentication
    content: |
      As an alternative to HTTP Basic authentication (`Username`/`Password`), authenticate with an
      OAuth 2.0 bearer token ([RFC 6750](https://www.rfc-editor.org/rfc/rfc6750)) by setting
      `BearerToken`. When set, it takes precedence and every request is sent with
      `Authorization: Bearer <token>`:

      ```csharp
      var options = new JmapClientOptions
      {
          SessionUrl = new Uri("https://jmap.example.test/.well-known/jmap"),
          BearerToken = "eyJhbGciOi...", // e.g. obtained via an OAuth 2.0 flow
      };

      using var client = new JmapClient(options);
      await client.ConnectAsync();
      ```
  - title: OAuth 2.0 bearer token authentication (2)
    content: |
      Multiple method calls can be batched into a single HTTP round trip via `SendRequestAsync`, using
      a `ResultReference` ([RFC 8620 §3.7](https://www.rfc-editor.org/rfc/rfc8620#section-3.7)) to
      chain a later call to an earlier one's result without a second request:

      ```csharp
      var query = new Invocation
      {
          Name = "Email/query",
          Arguments = new Dictionary<string, object> { ["accountId"] = accountId },
          MethodCallId = "c1",
      };
      var get = new Invocation
      {
          Name = "Email/get",
          Arguments = new Dictionary<string, object>
          {
              ["accountId"] = accountId,
              ["#ids"] = new ResultReference { ResultOf = "c1", Name = "Email/query", Path = "/ids" },
          },
          MethodCallId = "c2",
      };
      var resp = await client.SendRequestAsync(new[] { query, get }, new[] { "urn:ietf:params:jmap:mail" });
      ```
faq:
  enable: false
  list: []
---
