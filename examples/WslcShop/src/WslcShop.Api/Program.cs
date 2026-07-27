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

builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen();

var app = builder.Build();

await using (var scope = app.Services.CreateAsyncScope())
{
    var database = scope.ServiceProvider
        .GetRequiredService<ShopDbContext>();

    await database.Database.MigrateAsync();
}

if (app.Environment.IsDevelopment())
{
    app.UseSwagger();
    app.UseSwaggerUI();
}

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