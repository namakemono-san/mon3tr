using System.Text.Json;
using Calculator.Models;
using Calculator.Services;

try
{
    var json = await Console.In.ReadToEndAsync();
    var batchArgs = JsonSerializer.Deserialize<BatchArgs>(json)
        ?? throw new InvalidOperationException("Failed to deserialize input JSON.");

    var calculator = new OsuPpCalculator();
    var result = calculator.ComputeBatch(batchArgs);

    Console.WriteLine(JsonSerializer.Serialize(new CalcRes("ok", result)));
    return 0;
}
catch (Exception ex)
{
    var error = JsonSerializer.Serialize(new { status = "error", message = ex.Message });
    Console.WriteLine(error);
    return 1;
}
