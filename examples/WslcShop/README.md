Dann sollten die Beispiele konsequent auf **Windows PowerShell + VS Code** angepasst werden. Die bisherigen Bash-Kommandos wie `source .venv/bin/activate`, Backslash-Zeilenumbrüche und `curl -H` passen nicht optimal zu deinem Workflow.

Wichtig: Die Microsoft-Dokumentation und das Repository sollten vor der Veröffentlichung noch einmal gegen die aktuell unterstützten WSLC-Befehle geprüft werden, da WSLC weiterhin eine neue beziehungsweise sich ändernde Technologie ist.

# Code Examples for Windows PowerShell and VS Code

All commands in this article are executed from **PowerShell 7** in the integrated terminal of **Visual Studio Code**.

## Open PowerShell in VS Code

Open the project folder in VS Code:

```powershell
code .
```

Open the integrated terminal with:

```text
Ctrl + `
```

Select PowerShell as the terminal profile:

```text
Terminal → New Terminal
Terminal → Select Default Profile → PowerShell
```

You can verify the current shell:

```powershell
$PSVersionTable.PSVersion
```

---

## Install WSL

Run PowerShell as Administrator:

```powershell
wsl --install
```

List installed distributions:

```powershell
wsl --list --verbose
```

Set Ubuntu to WSL 2 if necessary:

```powershell
wsl --set-version Ubuntu 2
```

Update WSL:

```powershell
wsl --update
```

Install the preview version when required:

```powershell
wsl --update --pre-release
```

Check the installed version:

```powershell
wsl --version
```

Restart WSL after an update:

```powershell
wsl --shutdown
```

---

## Verify WSLC

Display the available commands:

```powershell
wslc --help
```

Check the installed version, if supported by the current preview:

```powershell
wslc version
```

Run a smoke test:

```powershell
wslc run --rm ubuntu:latest bash -c "echo Hello from WSLC"
```

Start an Nginx container:

```powershell
wslc run -d `
  --name web `
  -p 8080:80 `
  nginx:latest
```

List running containers:

```powershell
wslc container ps
```

Test the published port:

```powershell
Invoke-WebRequest `
  -Uri "http://localhost:8080"
```

Stop the container:

```powershell
wslc container stop web
```

---

## Install the Required Development Tools

Check the .NET SDK:

```powershell
dotnet --info
```

Check Git:

```powershell
git --version
```

Check Python:

```powershell
py --version
```

Check pip:

```powershell
py -m pip --version
```

Check Visual Studio Code:

```powershell
code --version
```

Recommended VS Code extensions:

```powershell
code --install-extension ms-dotnettools.csdevkit
code --install-extension ms-dotnettools.vscode-dotnet-runtime
code --install-extension redhat.vscode-yaml
code --install-extension ms-vscode.powershell
```

---

## Clone and Install `wslc-compose`

Create a development directory:

```powershell
New-Item `
  -ItemType Directory `
  -Path "$HOME\source" `
  -Force

Set-Location "$HOME\source"
```

Clone the repository:

```powershell
git clone https://github.com/yovannyr/wslc-compose.git
Set-Location .\wslc-compose
```

Create a Windows Python virtual environment:

```powershell
py -m venv .venv
```

Activate it in PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

After activation, the prompt should begin with:

```text
(.venv)
```

If PowerShell blocks the activation script, allow locally created scripts for the current user:

```powershell
Set-ExecutionPolicy `
  -ExecutionPolicy RemoteSigned `
  -Scope CurrentUser
```

Then activate the environment again:

```powershell
.\.venv\Scripts\Activate.ps1
```

Upgrade pip:

```powershell
python -m pip install --upgrade pip
```

Install the repository in editable mode:

```powershell
python -m pip install -e .
```

Verify the installation:

```powershell
wslc-compose --help
```

Check whether the wrapper syntax is available:

```powershell
wslc compose --help
```

Preview the generated WSLC commands:

```powershell
wslc-compose --dry-run up -d
```

Deactivate the Python environment later with:

```powershell
deactivate
```

---

## Create the .NET 10 Solution

Create the project directory:

```powershell
New-Item `
  -ItemType Directory `
  -Path "$HOME\source\WslcShop" `
  -Force

Set-Location "$HOME\source\WslcShop"
```

Create the solution and API:

```powershell
dotnet new sln -n WslcShop

dotnet new webapi `
  -n WslcShop.Api `
  -o .\src\WslcShop.Api

dotnet sln .\WslcShop.sln add `
  .\src\WslcShop.Api\WslcShop.Api.csproj
```

Open the project in VS Code:

```powershell
code .
```

---

## Install the NuGet Packages

```powershell
dotnet add `
  .\src\WslcShop.Api\WslcShop.Api.csproj `
  package Npgsql.EntityFrameworkCore.PostgreSQL
```

```powershell
dotnet add `
  .\src\WslcShop.Api\WslcShop.Api.csproj `
  package Microsoft.Extensions.Caching.StackExchangeRedis
```

```powershell
dotnet add `
  .\src\WslcShop.Api\WslcShop.Api.csproj `
  package Microsoft.Extensions.Diagnostics.HealthChecks.EntityFrameworkCore
```

```powershell
dotnet add `
  .\src\WslcShop.Api\WslcShop.Api.csproj `
  package AspNetCore.HealthChecks.Redis
```

Restore all packages:

```powershell
dotnet restore .\WslcShop.sln
```

Build the solution:

```powershell
dotnet build .\WslcShop.sln
```

---

## Project Structure

The project should look like this:

```text
WslcShop/
├── compose.yaml
├── .env
├── WslcShop.sln
└── src/
    └── WslcShop.Api/
        ├── Dockerfile
        ├── Program.cs
        ├── appsettings.json
        ├── appsettings.Development.json
        └── WslcShop.Api.csproj
```

Create missing files from PowerShell:

```powershell
New-Item `
  -ItemType File `
  -Path .\compose.yaml `
  -Force

New-Item `
  -ItemType File `
  -Path .\src\WslcShop.Api\Dockerfile `
  -Force
```

Open `compose.yaml` in VS Code:

```powershell
code .\compose.yaml
```

Open the Dockerfile:

```powershell
code .\src\WslcShop.Api\Dockerfile
```

---

## `Program.cs`

Replace the contents of `src\WslcShop.Api\Program.cs` with:

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

Build the project after saving:

```powershell
dotnet build .\WslcShop.sln
```

---

## `appsettings.json`

Use this content in:

```text
src\WslcShop.Api\appsettings.json
```

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

Open the file directly from PowerShell:

```powershell
code .\src\WslcShop.Api\appsettings.json
```

---

## `Dockerfile`

Use this content in:

```text
src\WslcShop.Api\Dockerfile
```

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

The backslashes in this file are Dockerfile continuation characters. They are not PowerShell line continuations.

---

## `compose.yaml`

Use this exact YAML structure:

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
      ConnectionStrings__Postgres: Host=postgres;Port=5432;Database=wslcshop;Username=postgres;Password=postgres
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

VS Code may suggest the Docker Compose schema after installing the YAML extension.

---

## Validate the YAML in PowerShell

When `wslc-compose` is installed, inspect the normalized configuration:

```powershell
wslc-compose config
```

Preview all generated commands:

```powershell
wslc-compose --dry-run up -d
```

This should be done before starting the stack for the first time.

---

## Start the Stack

From the `WslcShop` project directory:

```powershell
Set-Location "$HOME\source\WslcShop"
```

Start all services:

```powershell
wslc-compose up -d
```

When the wrapper is installed, you may alternatively use:

```powershell
wslc compose up -d
```

List project services:

```powershell
wslc-compose ps
```

Follow the API logs:

```powershell
wslc-compose logs -f api
```

Show PostgreSQL logs:

```powershell
wslc-compose logs postgres
```

Show Redis logs:

```powershell
wslc-compose logs redis
```

Run a command in the API container:

```powershell
wslc-compose exec api sh
```

Stop and remove the containers:

```powershell
wslc-compose down
```

Remove containers and project volumes:

```powershell
wslc-compose down -v
```

The `-v` option deletes PostgreSQL and Redis data stored in the project volumes.

---

## Entity Framework Core Migrations

Install or update the EF Core tool:

```powershell
dotnet tool install `
  --global dotnet-ef
```

If it is already installed:

```powershell
dotnet tool update `
  --global dotnet-ef
```

Create the first migration:

```powershell
dotnet ef migrations add InitialCreate `
  --project .\src\WslcShop.Api\WslcShop.Api.csproj `
  --startup-project .\src\WslcShop.Api\WslcShop.Api.csproj
```

Apply the migration:

```powershell
dotnet ef database update `
  --project .\src\WslcShop.Api\WslcShop.Api.csproj `
  --startup-project .\src\WslcShop.Api\WslcShop.Api.csproj
```

When this command runs on Windows, it connects through the published PostgreSQL port:

```text
Host=localhost;Port=5432
```

Inside the API container, the connection uses:

```text
Host=postgres;Port=5432
```

---

## Test the API from PowerShell

Create a request body:

```powershell
$product = @{
    name  = "Mechanical Keyboard"
    price = 149.99
}
```

Convert the body to JSON and send the request:

```powershell
$response = Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8080/products" `
  -ContentType "application/json" `
  -Body ($product | ConvertTo-Json)
```

Display the response:

```powershell
$response
```

Store the generated ID:

```powershell
$productId = $response.id
```

Read the product:

```powershell
Invoke-RestMethod `
  -Method Get `
  -Uri "http://localhost:8080/products/$productId"
```

Check the readiness endpoint:

```powershell
Invoke-WebRequest `
  -Uri "http://localhost:8080/health/ready"
```

Display only the status code:

```powershell
(Invoke-WebRequest `
  -Uri "http://localhost:8080/health/ready").StatusCode
```

Expected result:

```text
200
```

---

## Test the API with a VS Code REST File

Install the REST Client extension:

```powershell
code --install-extension humao.rest-client
```

Create a file named:

```text
requests.http
```

Add the following requests:

```http
@baseUrl = http://localhost:8080

### Check readiness

GET {{baseUrl}}/health/ready

### Create a product

POST {{baseUrl}}/products
Content-Type: application/json

{
  "name": "Mechanical Keyboard",
  "price": 149.99
}

### Read a product

GET {{baseUrl}}/products/REPLACE_WITH_PRODUCT_ID
```

VS Code displays a **Send Request** action above each request.

This is usually more convenient than translating every HTTP example into PowerShell manually.

---

## Manual WSLC Commands Without Compose

Create the network:

```powershell
wslc network create wslc-shop-network
```

Create persistent volumes:

```powershell
wslc volume create wslc-shop-postgres-data
wslc volume create wslc-shop-redis-data
```

Start PostgreSQL:

```powershell
wslc run -d `
  --name wslc-shop-postgres `
  --network wslc-shop-network `
  --network-alias postgres `
  -e POSTGRES_DB=wslcshop `
  -e POSTGRES_USER=postgres `
  -e POSTGRES_PASSWORD=postgres `
  -v wslc-shop-postgres-data:/var/lib/postgresql/data `
  postgres:17
```

Start Redis:

```powershell
wslc run -d `
  --name wslc-shop-redis `
  --network wslc-shop-network `
  --network-alias redis `
  -v wslc-shop-redis-data:/data `
  redis:7.4-alpine `
  redis-server --appendonly yes
```

Build the API image:

```powershell
wslc build `
  -t wslc-shop-api `
  -f .\src\WslcShop.Api\Dockerfile `
  .
```

Start the API:

```powershell
wslc run -d `
  --name wslc-shop-api `
  --network wslc-shop-network `
  -p 8080:8080 `
  -e ASPNETCORE_ENVIRONMENT=Development `
  -e "ConnectionStrings__Postgres=Host=postgres;Port=5432;Database=wslcshop;Username=postgres;Password=postgres" `
  -e "ConnectionStrings__Redis=redis:6379" `
  wslc-shop-api
```

---

## Useful VS Code Tasks

Create:

```text
.vscode\tasks.json
```

Add:

```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "WSLC Compose: Up",
      "type": "shell",
      "command": "wslc-compose",
      "args": [
        "up",
        "-d"
      ],
      "options": {
        "cwd": "${workspaceFolder}"
      },
      "problemMatcher": []
    },
    {
      "label": "WSLC Compose: Down",
      "type": "shell",
      "command": "wslc-compose",
      "args": [
        "down"
      ],
      "options": {
        "cwd": "${workspaceFolder}"
      },
      "problemMatcher": []
    },
    {
      "label": "WSLC Compose: API Logs",
      "type": "shell",
      "command": "wslc-compose",
      "args": [
        "logs",
        "-f",
        "api"
      ],
      "options": {
        "cwd": "${workspaceFolder}"
      },
      "problemMatcher": []
    },
    {
      "label": ".NET: Build",
      "type": "process",
      "command": "dotnet",
      "args": [
        "build",
        "${workspaceFolder}\\WslcShop.sln"
      ],
      "problemMatcher": "$msCompile"
    }
  ]
}
```

Run a task in VS Code through:

```text
Terminal → Run Task
```

Available tasks include:

```text
WSLC Compose: Up
WSLC Compose: Down
WSLC Compose: API Logs
.NET: Build
```

---

## Recommended PowerShell Workflow

Start VS Code:

```powershell
Set-Location "$HOME\source\WslcShop"
code .
```

Activate the Python environment containing `wslc-compose` when it was installed locally:

```powershell
& "$HOME\source\wslc-compose\.venv\Scripts\Activate.ps1"
```

Validate the configuration:

```powershell
wslc-compose config
```

Preview the runtime commands:

```powershell
wslc-compose --dry-run up -d
```

Start the environment:

```powershell
wslc-compose up -d
```

Follow the application logs:

```powershell
wslc-compose logs -f api
```

Test the API using `requests.http` or `Invoke-RestMethod`.

Stop the environment:

```powershell
wslc-compose down
```

Die wichtigste Korrektur ist die Installation von `wslc-compose`: Unter Windows PowerShell wird die virtuelle Umgebung mit `.\.venv\Scripts\Activate.ps1` aktiviert, nicht mit `source .venv/bin/activate`. Außerdem sollten HTTP-Beispiele vorzugsweise `Invoke-RestMethod` oder eine VS-Code-Datei `requests.http` verwenden.