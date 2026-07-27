# Running .NET 10, PostgreSQL, and Redis with Microsoft’s New WSL Containers

## How `wslc-compose` brings a Compose-style developer experience to Microsoft’s emerging Windows container runtime

For years, running a production-like development environment on Windows has usually meant installing Docker Desktop, writing a `compose.yaml`, and starting the complete stack with one command:

```bash
docker compose up --build
```

That workflow is popular because it solves more than container startup.

Compose creates networks, provisions volumes, injects configuration, builds application images, starts services in the correct order, and gives developers a project-level interface for logs, cleanup, and troubleshooting.

Microsoft’s new **WSL Container** feature introduces another way to run Linux containers on Windows.

Its command-line interface, `wslc.exe`, can pull images, build images, run containers, publish ports, create networks, manage volumes, and execute commands inside containers.

Microsoft also provides a programmable API that allows C#, C++, and C applications to manage Linux containers as part of their own application logic.

There is only one significant problem for normal application development:

> Microsoft’s current WSL Container tooling does not yet provide native Docker Compose support.

That makes `wslc` useful for individual containers, but less convenient for applications that depend on several services.

The open-source project [`wslc-compose`](https://github.com/yovannyr/wslc-compose) temporarily fills that gap.

In this article, we will use it to run a realistic local stack containing:

- a .NET 10 ASP.NET Core API
- PostgreSQL 17
- Redis 7.4
- persistent data volumes
- service-name-based networking
- environment-based configuration
- Entity Framework Core
- readiness checks
- a Compose-style development workflow

---

# The Architecture

Our example application stores products in PostgreSQL and uses Redis as a cache.

The API first checks Redis when a product is requested. If the entry does not exist, it loads the product from PostgreSQL and stores the response in Redis for a limited time.

## Graphic 1: Application architecture

```text
┌────────────────────────────────────────────────────────────┐
│                     Windows 11 Host                        │
│                                                            │
│  Browser / curl                                            │
│       │                                                    │
│       │  http://localhost:8080                             │
│       ▼                                                    │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              WSL Container Runtime                  │  │
│  │                                                      │  │
│  │   ┌───────────────────┐                              │  │
│  │   │  .NET 10 API     │                              │  │
│  │   │  ASP.NET Core    │                              │  │
│  │   └─────────┬─────────┘                              │  │
│  │             │                                        │  │
│  │      ┌──────┴──────┐                                 │  │
│  │      │             │                                 │  │
│  │      ▼             ▼                                 │  │
│  │ ┌────────────┐  ┌────────────┐                       │  │
│  │ │ PostgreSQL │  │   Redis    │                       │  │
│  │ │ Source of  │  │   Cache    │                       │  │
│  │ │ truth      │  │            │                       │  │
│  │ └────────────┘  └────────────┘                       │  │
│  │                                                      │  │
│  └──────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────┘
```

The application architecture is intentionally conventional.

What changes is not the .NET code or the container images. What changes is the runtime used to execute them.

---

# What Is WSLC?

The naming can be confusing, so it helps to separate WSL from WSLC.

## WSL

**Windows Subsystem for Linux** allows Windows users to run Linux distributions and Linux command-line tools without maintaining a traditional virtual machine manually.

Developers commonly use WSL for:

- Bash
- Git
- .NET
- Node.js
- Python
- Linux package managers
- build tools
- automation scripts

WSL itself is broader than containers.

It provides the Linux environment on which Microsoft’s new container feature is built.

## WSLC

**WSL Containers**, or WSLC, is Microsoft’s new container functionality integrated into WSL.

Its command-line interface is:

```powershell
wslc.exe
```

The CLI offers commands for familiar container concepts:

```powershell
wslc run
wslc build
wslc exec
wslc image ls
wslc container ps
wslc network create
wslc volume create
```

For example, an Ubuntu container can be started with:

```powershell
wslc run --rm -it ubuntu:latest bash
```

An Nginx server can be started with a published port:

```powershell
wslc run -d `
  --name web `
  -p 8080:80 `
  nginx:latest
```

The running container can then be inspected:

```powershell
wslc container ps
```

And accessed from Windows:

```powershell
curl http://localhost:8080
```

The experience is intentionally familiar to developers who already use container tooling.

---

# WSLC Is More Than a Command-Line Tool

Microsoft’s WSL Container feature has two major parts:

1. the `wslc.exe` command-line interface
2. the WSL Container API

The CLI targets developers and administrators working from a terminal.

The API targets Windows application developers who want to use Linux containers as part of their own software.

For .NET, Microsoft provides the following NuGet package:

```bash
dotnet add package Microsoft.WSL.Containers
```

A Windows application can then work with concepts such as:

- `WslcService`
- `Session`
- `Container`
- `Process`

The object model roughly follows the container lifecycle.

## Graphic 2: WSLC programming model

```text
┌───────────────────────────┐
│       Windows App         │
│     C# / C++ / C          │
└─────────────┬─────────────┘
              │
              ▼
┌───────────────────────────┐
│       WslcService         │
│                           │
│ Check installation        │
│ Query service version     │
│ Install dependencies      │
└─────────────┬─────────────┘
              │
              ▼
┌───────────────────────────┐
│         Session           │
│                           │
│ Pull and build images     │
│ Host containers           │
│ Allocate resources        │
└─────────────┬─────────────┘
              │
              ▼
┌───────────────────────────┐
│        Container          │
│                           │
│ Start / stop / inspect    │
│ Mount files and volumes   │
│ Configure networking      │
└─────────────┬─────────────┘
              │
              ▼
┌───────────────────────────┐
│         Process           │
│                           │
│ stdin / stdout / stderr   │
│ Signals and exit codes    │
└───────────────────────────┘
```

This API creates interesting possibilities for:

- desktop developer tools
- disposable integration-test environments
- build sandboxes
- embedded Linux toolchains
- isolated plug-ins
- local database provisioning
- AI and GPU workloads
- compatibility test runners

A Windows application can control Linux containers directly instead of treating an external desktop application as its only integration point.

---

# Is WSLC Just Docker Inside WSL?

Not exactly.

The concepts are similar, but the platform architecture is different.

In a typical Docker Desktop workflow, Docker Desktop provides the engine, Compose integration, networking, storage, UI, and Windows integration.

With WSLC, Microsoft integrates container functionality directly into the WSL platform and exposes it through its own CLI and application API.

## Graphic 3: Runtime comparison

```text
TRADITIONAL DOCKER DESKTOP WORKFLOW

Developer
    │
    ▼
Docker CLI / Docker Compose
    │
    ▼
Docker Desktop
    │
    ▼
Linux environment and container engine
    │
    ▼
Containers
```

```text
WSL CONTAINER WORKFLOW

Developer or Windows application
    │
    ├───────────────┐
    ▼               ▼
wslc.exe     Microsoft.WSL.Containers API
    │               │
    └───────┬───────┘
            ▼
    WSL Container service
            │
            ▼
       Linux containers
```

This does not mean that Docker has suddenly become obsolete.

It means Windows developers now have an additional runtime architecture to evaluate.

---

# Installing WSLC

WSLC is delivered as part of WSL rather than as a separate container desktop application.

Because it is still an evolving feature, the exact installation requirements may change. Check Microsoft’s current documentation before adopting it for a team.

## Step 1: Install WSL

Open PowerShell as an administrator:

```powershell
wsl --install
```

Restart Windows when requested.

Check the installed distributions:

```powershell
wsl --list --verbose
```

A typical result looks like this:

```text
NAME      STATE    VERSION
Ubuntu    Running  2
```

Your distribution should use WSL 2.

If necessary:

```powershell
wsl --set-version Ubuntu 2
```

## Step 2: Update WSL

Update to the current WSL release:

```powershell
wsl --update
```

When the container feature is only available through the preview channel, use:

```powershell
wsl --update --pre-release
```

Check the installed version:

```powershell
wsl --version
```

## Step 3: Verify the WSLC CLI

Check whether `wslc` is available:

```powershell
wslc --help
```

Depending on the current CLI version, version information may be available through:

```powershell
wslc version
```

## Step 4: Run a smoke test

Start a temporary container:

```powershell
wslc run --rm -it ubuntu:latest bash -c "echo Hello from WSLC"
```

This verifies that:

- the runtime is installed
- an image can be downloaded
- a container can be created
- a Linux process can run
- output can be returned to the terminal

## Step 5: Test port publishing

Start Nginx:

```powershell
wslc run -d `
  --name web `
  -p 8080:80 `
  nginx:latest
```

Inspect the running container:

```powershell
wslc container ps
```

Open the service:

```powershell
curl http://localhost:8080
```

Stop it again:

```powershell
wslc container stop web
```

At this point, the runtime itself is ready.

The problem begins when we need more than one container.

---

# Why WSLC Is Interesting

WSLC is still young, but it introduces several compelling ideas.

## 1. It is integrated into WSL

The runtime is part of Microsoft’s WSL platform.

Developers do not have to install another Linux distribution manually just to host an engine.

This can reduce the number of infrastructure layers in a Windows development environment.

## 2. It is a first-party Microsoft component

For organizations already using Windows, WSL, PowerShell, Visual Studio, Microsoft Defender, and enterprise management tooling, first-party integration can be important.

Potential integration areas include:

- Windows security policies
- Windows networking
- GPU support
- Microsoft Defender
- managed enterprise devices
- Visual Studio
- MSBuild
- PowerShell automation

Not every integration is equally mature today, but the architectural direction is relevant.

## 3. It provides a native .NET API

The `Microsoft.WSL.Containers` package is one of the most distinctive parts of the platform.

It lets a .NET application manage Linux container workloads programmatically.

That could be especially valuable for developer-tool vendors and enterprise desktop software.

## 4. It uses familiar container concepts

Developers still work with:

- images
- containers
- registries
- ports
- networks
- volumes
- mounts
- processes
- logs

The model does not require learning an entirely new vocabulary.

## 5. It creates another option for Windows development

Teams may eventually choose between container runtimes based on:

- enterprise policies
- licensing
- security requirements
- Compose support
- application integration
- performance
- CI compatibility
- development tooling

A new option does not have to replace every existing option to be useful.

---

# The Current Disadvantages

WSLC should not yet be presented as a complete replacement for Docker Desktop.

## 1. It is still an emerging platform

Preview software may introduce:

- breaking CLI changes
- API changes
- runtime bugs
- incomplete documentation
- inconsistent behavior between versions

It is appropriate for evaluation and local experiments, but production adoption requires careful validation.

## 2. Native Compose support is missing

This is the main problem for application developers.

Starting one container is straightforward:

```powershell
wslc run redis:7.4-alpine
```

Starting a complete application stack is not.

## 3. The ecosystem is still small

Docker has years of:

- documentation
- examples
- IDE integrations
- troubleshooting knowledge
- third-party tools
- community support

WSLC does not yet have that level of ecosystem maturity.

## 4. Feature parity is incomplete

Some advanced container and Compose capabilities may not map cleanly to the current WSLC runtime.

Possible limitations include:

- health-check orchestration
- restart policies
- Compose secrets
- Compose configs
- device mappings
- privileged containers
- additional Linux capabilities
- advanced network topologies
- custom logging drivers

## 5. CI and local environments may differ

Many CI platforms use Docker or another Docker-compatible runtime.

A stack that works locally with WSLC must still be tested against the runtime used in CI and production.

## 6. It is Windows-specific

Teams that use Windows, macOS, and Linux may still prefer a cross-platform runtime as their common developer baseline.

---

# Why the Missing Compose Support Matters

Running a single container is not the same as running an application environment.

Our example needs:

- an application image
- a PostgreSQL image
- a Redis image
- a private network
- service DNS names
- two persistent volumes
- environment variables
- published ports
- dependency ordering
- predictable cleanup

Without Compose, the workflow becomes a long sequence of commands.

A simplified manual setup could look like this:

```powershell
wslc network create wslc-shop-network

wslc volume create wslc-shop-postgres-data
wslc volume create wslc-shop-redis-data

wslc run -d `
  --name wslc-shop-postgres `
  --network wslc-shop-network `
  --network-alias postgres `
  -e POSTGRES_DB=wslcshop `
  -e POSTGRES_USER=postgres `
  -e POSTGRES_PASSWORD=postgres `
  -v wslc-shop-postgres-data:/var/lib/postgresql/data `
  postgres:17

wslc run -d `
  --name wslc-shop-redis `
  --network wslc-shop-network `
  --network-alias redis `
  -v wslc-shop-redis-data:/data `
  redis:7.4-alpine `
  redis-server --appendonly yes

wslc build `
  -t wslc-shop-api `
  -f src/WslcShop.Api/Dockerfile .

wslc run -d `
  --name wslc-shop-api `
  --network wslc-shop-network `
  -p 8080:8080 `
  -e ASPNETCORE_ENVIRONMENT=Development `
  -e ConnectionStrings__Postgres="Host=postgres;Port=5432;Database=wslcshop;Username=postgres;Password=postgres" `
  -e ConnectionStrings__Redis="redis:6379" `
  wslc-shop-api
```

That may be acceptable for a quick experiment.

It is not an ideal daily workflow.

Developers also need repeatable commands for:

- stopping everything
- recreating changed containers
- viewing logs
- opening a shell
- rebuilding images
- removing the project
- preserving or deleting data
- starting only selected services

Compose solves this by moving the desired application topology into a declarative file.

---

# The Gap in One Diagram

## Graphic 4: What WSLC provides—and what is missing

```text
┌──────────────────────────────────────────────┐
│              MICROSOFT WSLC                  │
├──────────────────────────────────────────────┤
│                                              │
│  ✓ Pull and build images                     │
│  ✓ Create and run containers                 │
│  ✓ Publish ports                             │
│  ✓ Create networks                           │
│  ✓ Create persistent volumes                 │
│  ✓ Inject environment variables              │
│  ✓ Execute commands                          │
│  ✓ Read logs                                 │
│  ✓ Programmatic C# / C++ / C API             │
│                                              │
│  ✗ No native compose.yaml orchestration      │
│                                              │
└──────────────────────────────────────────────┘
                       │
                       │ Missing layer
                       ▼
┌──────────────────────────────────────────────┐
│               WSLC-COMPOSE                   │
├──────────────────────────────────────────────┤
│                                              │
│  Reads compose.yaml                          │
│  Resolves variables                          │
│  Calculates dependency order                 │
│  Creates project resources                   │
│  Builds or pulls images                      │
│  Generates and executes wslc commands        │
│                                              │
└──────────────────────────────────────────────┘
```

That missing layer has a disproportionate effect.

Without it, WSLC is comfortable for individual containers but cumbersome for realistic multi-service projects.

---

# How `wslc-compose` Solves the Problem

The project used in this article is:

[https://github.com/yovannyr/wslc-compose](https://github.com/yovannyr/wslc-compose)

`wslc-compose` is a thin orchestration layer.

It does not implement another container runtime.

It does not run its own daemon.

It does not replace Microsoft’s `wslc.exe`.

Instead, it:

1. reads an existing Compose file
2. loads `.env` values and environment variables
3. normalizes the service configuration
4. determines which networks and volumes are required
5. determines which images must be built or pulled
6. sorts services according to `depends_on`
7. checks whether existing containers are outdated
8. executes normal `wslc` commands

## Graphic 5: How the extension works

```text
┌─────────────────────┐
│    compose.yaml     │
│                     │
│ Services            │
│ Networks            │
│ Volumes             │
│ Environment         │
│ Dependencies        │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│    wslc-compose     │
│                     │
│ Parse               │
│ Normalize           │
│ Validate            │
│ Plan                │
│ Compare state       │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Generated commands  │
│                     │
│ wslc build          │
│ wslc run            │
│ wslc network create │
│ wslc volume create  │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Microsoft WSLC      │
│                     │
│ Images              │
│ Containers          │
│ Networks            │
│ Volumes             │
└─────────────────────┘
```

One of the project’s most useful design choices is its `--dry-run` mode.

```bash
wslc-compose --dry-run up -d
```

Instead of changing the environment, the tool prints the generated `wslc` commands.

That makes the abstraction transparent.

There is no hidden second container engine and no mysterious orchestration service.

---

# What `wslc-compose` Does Not Do

The extension has clear boundaries.

It does not:

- emulate Linux containers
- replace the WSL kernel
- implement an additional image format
- run a separate container daemon
- add runtime features that WSLC itself does not expose
- guarantee complete Docker Compose compatibility

This distinction is important.

If WSLC does not yet support a specific low-level feature, a Compose adapter cannot reproduce it perfectly.

The adapter can only:

- translate it
- approximate it
- reject it
- or warn that it is unsupported

`wslc-compose` solves the orchestration gap.

It does not magically eliminate every limitation of the underlying preview runtime.

---

# Why This Extension Is Useful for Now

The words **for now** are important.

Microsoft may eventually provide first-party Compose support.

Until then, `wslc-compose` offers three practical advantages.

## Existing Compose knowledge remains useful

Developers do not have to replace their Compose files with large PowerShell or Bash scripts.

## Existing application topology can be reused

The same `compose.yaml` can remain useful for:

- Docker Compose
- Podman Compose
- WSLC evaluation
- documentation
- onboarding
- local integration testing

Not every setting will behave identically, but the architecture remains portable.

## Teams can evaluate realistic applications

Without an adapter, WSLC evaluation tends to stop at examples such as Nginx, Redis, or `hello-world`.

With `wslc-compose`, teams can test actual multi-service applications before Microsoft ships native orchestration.

---

# Installing `wslc-compose`

The repository provides several installation paths.

For this article, we will install the fork directly from its source.

Clone it inside a WSL distribution:

```bash
git clone https://github.com/yovannyr/wslc-compose.git
cd wslc-compose
```

Create a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the package:

```bash
python3 -m pip install -e .
```

Verify the standalone command:

```bash
wslc-compose --help
```

The project can also provide a wrapper that supports the familiar form:

```bash
wslc compose --help
```

Commands that are not Compose commands can continue to be forwarded to the original WSLC CLI.

For example:

```bash
wslc compose up -d
wslc compose ps
wslc image ls
wslc container ps
```

---

# Creating the .NET 10 Project

Create the solution:

```bash
mkdir WslcShop
cd WslcShop

dotnet new sln -n WslcShop
dotnet new webapi -n WslcShop.Api -o src/WslcShop.Api

dotnet sln add src/WslcShop.Api/WslcShop.Api.csproj
```

Install the required packages:

```bash
dotnet add src/WslcShop.Api package Npgsql.EntityFrameworkCore.PostgreSQL
dotnet add src/WslcShop.Api package Microsoft.Extensions.Caching.StackExchangeRedis
dotnet add src/WslcShop.Api package Microsoft.Extensions.Diagnostics.HealthChecks.EntityFrameworkCore
dotnet add src/WslcShop.Api package AspNetCore.HealthChecks.Redis
```

The project structure looks like this:

```text
WslcShop/
├── compose.yaml
├── .env
└── src/
    └── WslcShop.Api/
        ├── Dockerfile
        ├── Program.cs
        ├── appsettings.json
        ├── appsettings.Development.json
        └── WslcShop.Api.csproj
```

---

# Implementing the API

The following example uses PostgreSQL as the database and Redis as a distributed cache.

```csharp
using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Caching.Distributed;

var builder = WebApplication.CreateBuilder(args);

var postgresConnection =
    builder.Configuration.GetConnectionString("Postgres")
    ?? throw new InvalidOperationException(
        "Postgres connection string is missing.");

var redisConnection =
    builder.Configuration.GetConnectionString("Redis")
    ?? throw new InvalidOperationException(
        "Redis connection string is missing.");

builder.Services.AddDbContext<ShopDbContext>(options =>
{
    options.UseNpgsql(
        postgresConnection,
        npgsql =>
        {
            npgsql.EnableRetryOnFailure(
                maxRetryCount: 5,
                maxRetryDelay: TimeSpan.FromSeconds(5),
                errorCodesToAdd: null);
        });
});

builder.Services.AddStackExchangeRedisCache(options =>
{
    options.Configuration = redisConnection;
    options.InstanceName = "wslc-shop:";
});

builder.Services
    .AddHealthChecks()
    .AddNpgSql(postgresConnection)
    .AddRedis(redisConnection);

var app = builder.Build();

app.MapPost(
    "/products",
    async (
        CreateProductRequest request,
        ShopDbContext database,
        CancellationToken cancellationToken) =>
    {
        var product = new Product
        {
            Id = Guid.NewGuid(),
            Name = request.Name,
            Price = request.Price
        };

        database.Products.Add(product);
        await database.SaveChangesAsync(cancellationToken);

        return Results.Created(
            $"/products/{product.Id}",
            new ProductResponse(
                product.Id,
                product.Name,
                product.Price));
    });

app.MapGet(
    "/products/{id:guid}",
    async (
        Guid id,
        ShopDbContext database,
        IDistributedCache cache,
        CancellationToken cancellationToken) =>
    {
        var cacheKey = $"products:{id}";

        var cachedJson =
            await cache.GetStringAsync(
                cacheKey,
                cancellationToken);

        if (cachedJson is not null)
        {
            var cachedProduct =
                JsonSerializer.Deserialize<ProductResponse>(
                    cachedJson);

            return Results.Ok(cachedProduct);
        }

        var product = await database.Products
            .AsNoTracking()
            .SingleOrDefaultAsync(
                product => product.Id == id,
                cancellationToken);

        if (product is null)
        {
            return Results.NotFound();
        }

        var response = new ProductResponse(
            product.Id,
            product.Name,
            product.Price);

        await cache.SetStringAsync(
            cacheKey,
            JsonSerializer.Serialize(response),
            new DistributedCacheEntryOptions
            {
                AbsoluteExpirationRelativeToNow =
                    TimeSpan.FromMinutes(10)
            },
            cancellationToken);

        return Results.Ok(response);
    });

app.MapHealthChecks("/health/ready");

app.Run();

public sealed record CreateProductRequest(
    string Name,
    decimal Price);

public sealed record ProductResponse(
    Guid Id,
    string Name,
    decimal Price);

public sealed class Product
{
    public Guid Id { get; set; }

    public required string Name { get; set; }

    public decimal Price { get; set; }
}

public sealed class ShopDbContext(
    DbContextOptions<ShopDbContext> options)
    : DbContext(options)
{
    public DbSet<Product> Products => Set<Product>();

    protected override void OnModelCreating(
        ModelBuilder modelBuilder)
    {
        modelBuilder.Entity<Product>(entity =>
        {
            entity.ToTable("products");
            entity.HasKey(product => product.Id);

            entity.Property(product => product.Name)
                .HasMaxLength(200)
                .IsRequired();

            entity.Property(product => product.Price)
                .HasPrecision(18, 2);
        });
    }
}
```

---

# Understanding the Cache Flow

## Graphic 6: Cache-aside request flow

```text
Client requests product
          │
          ▼
┌─────────────────────┐
│ Check Redis cache   │
└──────────┬──────────┘
           │
     ┌─────┴─────┐
     │           │
 Cache hit    Cache miss
     │           │
     ▼           ▼
Return       Query PostgreSQL
cached            │
response          ▼
            Store response
              in Redis
                  │
                  ▼
            Return response
```

PostgreSQL remains the source of truth.

Redis accelerates repeated reads but does not become the authoritative data store.

---

# Configuration

Use local defaults in `appsettings.json`:

```json
{
  "ConnectionStrings": {
    "Postgres": "Host=localhost;Port=5432;Database=wslcshop;Username=postgres;Password=postgres",
    "Redis": "localhost:6379"
  },
  "Logging": {
    "LogLevel": {
      "Default": "Information",
      "Microsoft.AspNetCore": "Warning"
    }
  },
  "AllowedHosts": "*"
}
```

These values work when the API runs directly on the host.

Inside the container network, environment variables override them.

The API must use service names rather than `localhost`:

```text
postgres:5432
redis:6379
```

Inside the API container, `localhost` refers to the API container itself.

---

# The Dockerfile

WSLC uses Linux container images, so a normal multi-stage Dockerfile remains useful.

```dockerfile
FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build

WORKDIR /src

COPY src/WslcShop.Api/WslcShop.Api.csproj \
     src/WslcShop.Api/

RUN dotnet restore \
    src/WslcShop.Api/WslcShop.Api.csproj

COPY . .

RUN dotnet publish \
    src/WslcShop.Api/WslcShop.Api.csproj \
    --configuration Release \
    --output /app/publish \
    --no-restore

FROM mcr.microsoft.com/dotnet/aspnet:10.0 AS runtime

WORKDIR /app

COPY --from=build /app/publish .

ENV ASPNETCORE_URLS=http://+:8080

EXPOSE 8080

ENTRYPOINT ["dotnet", "WslcShop.Api.dll"]
```

The SDK image builds the application.

The smaller ASP.NET runtime image runs it.

---

# The Compose File

Create `compose.yaml`:

```yaml
name: wslc-shop

services:
  api:
    build:
      context: .
      dockerfile: src/WslcShop.Api/Dockerfile

    ports:
      - "8080:8080"

    environment:
      ASPNETCORE_ENVIRONMENT: Development
      ConnectionStrings__Postgres: >-
        Host=postgres;
        Port=5432;
        Database=wslcshop;
        Username=postgres;
        Password=postgres
      ConnectionStrings__Redis: redis:6379

    depends_on:
      - postgres
      - redis

  postgres:
    image: postgres:17

    environment:
      POSTGRES_DB: wslcshop
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres

    ports:
      - "5432:5432"

    volumes:
      - postgres-data:/var/lib/postgresql/data

  redis:
    image: redis:7.4-alpine

    command:
      - redis-server
      - --appendonly
      - "yes"

    ports:
      - "6379:6379"

    volumes:
      - redis-data:/data

volumes:
  postgres-data:
  redis-data:
```

This is the central value of the adapter.

We can keep a declarative Compose file rather than manually maintaining a long list of runtime commands.

---

# Starting the Stack

First, inspect the commands that would be executed:

```bash
wslc-compose --dry-run up -d
```

Then start the services:

```bash
wslc compose up -d
```

Or use the standalone command:

```bash
wslc-compose up -d
```

Inspect the project:

```bash
wslc compose ps
```

Follow the API logs:

```bash
wslc compose logs -f api
```

Open a shell:

```bash
wslc compose exec api sh
```

Stop and remove the containers:

```bash
wslc compose down
```

Remove the project volumes as well:

```bash
wslc compose down -v
```

Use `-v` carefully. It removes the persistent PostgreSQL and Redis data associated with the project.

---

# Running Entity Framework Migrations

Install the EF Core command-line tool:

```bash
dotnet tool install --global dotnet-ef
```

Create the first migration:

```bash
dotnet ef migrations add InitialCreate \
  --project src/WslcShop.Api \
  --startup-project src/WslcShop.Api
```

Apply it:

```bash
dotnet ef database update \
  --project src/WslcShop.Api \
  --startup-project src/WslcShop.Api
```

From the host, PostgreSQL is reached through:

```text
Host=localhost;Port=5432
```

From another container, use:

```text
Host=postgres;Port=5432
```

In production, migrations should normally run as a dedicated deployment step rather than from every application replica during startup.

---

# Testing the Application

Create a product:

```bash
curl -X POST http://localhost:8080/products \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Mechanical Keyboard",
    "price": 149.99
  }'
```

The API returns a generated ID:

```json
{
  "id": "929b5d5d-b981-498f-a0f1-61edb7ea6ffc",
  "name": "Mechanical Keyboard",
  "price": 149.99
}
```

Read the product:

```bash
curl http://localhost:8080/products/929b5d5d-b981-498f-a0f1-61edb7ea6ffc
```

The first request loads the product from PostgreSQL and stores it in Redis.

Subsequent requests can be served from the cache until the entry expires.

Check readiness:

```bash
curl http://localhost:8080/health/ready
```

---

# An Important Preview Limitation: Startup Is Not Readiness

A Compose file may declare:

```yaml
depends_on:
  - postgres
  - redis
```

This determines startup order.

It does not necessarily mean that PostgreSQL is ready to accept connections when the API starts.

There is an important difference:

```text
Container process started
           ≠
Service is ready
```

A mature Compose environment can combine health checks with dependency conditions.

If the WSLC preview does not yet expose all the required health-check behavior, applications should tolerate temporarily unavailable dependencies.

That is why the Npgsql configuration in the example uses bounded retry logic.

Retries should remain limited.

An application should not retry forever when configuration, credentials, or networking are fundamentally broken.

---

# Benefits and Trade-Offs

WSLC is promising, but it is not a simple “better or worse” alternative to Docker Desktop. Its value depends heavily on the project, the team, and the required feature set.

## Where WSLC Is Strong

### Native Windows and WSL integration

WSLC is part of Microsoft’s WSL ecosystem. That makes it especially interesting for teams that already use Windows, WSL, PowerShell, Visual Studio, and Microsoft development tooling.

**Benefit:** fewer separate platform layers and a more direct integration with Windows.

### First-party Microsoft runtime

The runtime, CLI, and API come from Microsoft rather than from a third-party desktop container platform.

**Benefit:** potential integration with Windows security, enterprise policies, GPU support, networking, and management tooling.

### Programmable .NET API

The `Microsoft.WSL.Containers` package allows Windows applications to create and manage Linux containers from C#.

**Benefit:** useful for developer tools, integration-test platforms, build systems, desktop applications, and isolated Linux workloads.

### Familiar container model

WSLC still uses concepts developers already know:

- images
- containers
- ports
- networks
- volumes
- registries
- logs
- processes

**Benefit:** developers do not need to learn a completely new operational model.

### No separate Compose-specific runtime

`wslc-compose` does not introduce another daemon or container engine. It translates Compose concepts into normal `wslc` commands.

**Benefit:** the runtime remains transparent and easier to inspect.

### Reuse of existing Compose files

Projects can keep using a familiar `compose.yaml` structure.

**Benefit:** less custom scripting and an easier migration path for existing applications.

---

## Where the Current Trade-Offs Are

### Preview maturity

WSLC is still an emerging platform.

**Trade-off:** CLI behavior, APIs, compatibility, and documentation may still change.

### No native Compose support

Microsoft’s runtime currently does not provide a built-in Compose workflow.

**Trade-off:** multi-container projects need an additional layer such as `wslc-compose`.

### Incomplete Compose compatibility

Some Compose features may not map directly to WSLC.

Examples include:

- advanced health checks
- restart policies
- secrets
- configs
- privileged containers
- device mappings
- additional Linux capabilities
- complex network topologies
- custom logging drivers

**Trade-off:** existing Compose files may require adjustments.

### Smaller ecosystem

Docker has a much larger ecosystem of documentation, integrations, extensions, and troubleshooting knowledge.

**Trade-off:** WSLC users should expect fewer examples and less mature third-party support.

### No mature desktop UI

WSLC is currently focused on CLI and API usage.

**Trade-off:** teams that depend on graphical container management may find the experience less convenient.

### Possible differences from CI and production

Many build pipelines and production environments still use Docker or another OCI-compatible runtime.

**Trade-off:** a stack that works locally with WSLC must still be validated against the actual deployment runtime.

### Windows-specific platform

WSLC is designed for Windows and WSL.

**Trade-off:** mixed teams using macOS, Linux, and Windows may still need another common development baseline.

---

## Quick Comparison

### Choose WSLC when you value:

- deep Windows and WSL integration
- a first-party Microsoft runtime
- programmatic container control from C#
- a lightweight CLI-oriented workflow
- experimentation with Windows-native container tooling
- standard multi-container development through `wslc-compose`

### Be cautious when you require:

- complete Docker Compose compatibility
- mature restart and health-check behavior
- advanced container privileges
- broad third-party integrations
- graphical management
- identical behavior across Windows, macOS, and Linux
- production-grade stability without preview risk

---

## Bottom Line

WSLC offers a compelling new direction for Windows-based container development.

Its strongest advantages are the native WSL integration, the Microsoft-supported runtime, and the programmable .NET API.

Its biggest weaknesses are still maturity, missing native Compose support, and incomplete compatibility with advanced Compose features.

That is exactly where `wslc-compose` becomes useful.

It preserves the familiar Compose workflow while delegating the actual container execution to Microsoft’s runtime. It does not eliminate every limitation, but it makes WSLC practical for realistic multi-container development today.

---

# When Should You Evaluate WSLC?

WSLC is worth exploring when:

- your developers primarily use Windows
- your services run on Linux
- you already rely heavily on WSL
- you build .NET developer tools
- you want to control Linux containers from C#
- you want an alternative Windows container architecture
- your Compose files use relatively standard features
- your organization is comfortable evaluating preview software

Be more cautious when:

- exact production parity is required
- your environment uses advanced Compose features
- your team works across Windows, macOS, and Linux
- preview software is prohibited
- you depend heavily on Docker Desktop extensions
- privileged containers or unusual device access are required
- you need a mature graphical management experience

---

# Is It a Docker Desktop Replacement?

Not universally, and not yet.

Docker Desktop currently offers a broader and more mature experience:

- native Compose
- a graphical interface
- extensive integrations
- established troubleshooting knowledge
- broad feature support
- a large ecosystem

WSLC offers a different value proposition:

- integration with WSL
- a first-party Microsoft runtime
- direct Windows tooling
- a programmable .NET API
- potential enterprise-policy integration
- a new path for Windows-native development tools

The more useful question is not:

> Which runtime wins?

It is:

> Which runtime best matches this team’s constraints?

---

# Final Architecture

## Graphic 7: Complete local workflow

```text
Developer
    │
    ▼
compose.yaml
    │
    ▼
┌─────────────────────────────┐
│        wslc-compose         │
│                             │
│ Parse configuration         │
│ Resolve variables           │
│ Create dependency order     │
│ Detect configuration drift  │
│ Plan runtime operations     │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│          wslc.exe           │
│                             │
│ Build images                │
│ Create networks             │
│ Create volumes              │
│ Run containers              │
│ Publish ports               │
└──────────────┬──────────────┘
               │
               ▼
┌────────────────────────────────────────────┐
│          WSL Container Runtime             │
│                                            │
│  ┌────────────┐ ┌────────────┐ ┌────────┐ │
│  │ .NET 10 API│ │ PostgreSQL │ │ Redis  │ │
│  └────────────┘ └────────────┘ └────────┘ │
│                                            │
└────────────────────────────────────────────┘
```

The responsibilities remain separate:

| Component | Responsibility |
|---|---|
| `compose.yaml` | Describes the application topology |
| `wslc-compose` | Interprets and orchestrates Compose concepts |
| `wslc.exe` | Executes container runtime commands |
| WSL Container runtime | Manages images, containers, networks, volumes, and processes |
| .NET application | Implements business logic |

---

# Final Thoughts

A good local development environment is an architectural choice.

It influences:

- onboarding speed
- reproducibility
- test reliability
- debugging
- configuration quality
- confidence before deployment

The original Docker Compose version of this stack can be started with:

```bash
docker compose up --build
```

Using Microsoft’s new runtime and the compatibility layer, the equivalent developer experience becomes:

```bash
wslc compose up -d
```

The Compose file still describes the application.

`wslc-compose` interprets that description.

Microsoft’s `wslc` runtime executes the containers.

This is not yet a perfect replacement for a mature Docker Compose environment. Current limitations around health checks, restart behavior, advanced mounts, devices, networking, and other Compose features still matter.

But the direction is technically significant.

Windows now has:

- a first-party Linux-container CLI
- a programmable container API for .NET
- direct integration with WSL
- a path toward deeper Windows security and management integration

Until Microsoft delivers native Compose support, `wslc-compose` provides a practical bridge.

It allows developers to evaluate realistic multi-container applications today, without abandoning the declarative workflow they already understand.

---

# Resources

- [`wslc-compose` repository](https://github.com/yovannyr/wslc-compose)
- [Microsoft WSL Container documentation](https://learn.microsoft.com/en-us/windows/wsl/wsl-container?tabs=csharp)
- [Microsoft WSL repository](https://github.com/microsoft/WSL)
- [.NET container images](https://mcr.microsoft.com/en-us/catalog?search=dotnet)
- [PostgreSQL container image](https://hub.docker.com/_/postgres)
- [Redis container image](https://hub.docker.com/_/redis)

---

# Suggested Medium Tags

`WSL` · `.NET` · `Containers` · `Windows` · `Docker Compose`