using System.Text.Json.Serialization;

namespace Calculator.Models;

public sealed class CalcRes(string status, ResultData result)
{
    [JsonPropertyName("status")]
    public string Status { get; } = status;

    [JsonPropertyName("result")]
    public ResultData Result { get; } = result;
}

public sealed class ResultData
{
    [JsonPropertyName("stars")]
    public double Stars { get; set; }

    [JsonPropertyName("scores")]
    public List<ScoreOut> Scores { get; set; } = [];
}

public sealed class ScoreOut
{
    [JsonPropertyName("accuracy")]
    public double Accuracy { get; set; }

    [JsonPropertyName("pp")]
    public double Pp { get; set; }
}
