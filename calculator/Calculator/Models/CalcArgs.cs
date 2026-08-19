using System.Text.Json.Serialization;

namespace Calculator.Models;

public class BatchArgs
{
    [JsonPropertyName("file")]   public string FilePath { get; set; } = string.Empty;
    [JsonPropertyName("mode")]   public int Mode { get; set; }
    [JsonPropertyName("mods")]   public int Mods { get; set; }
    [JsonPropertyName("combo")]  public int Combo { get; set; }
    [JsonPropertyName("scores")] public List<ScoreArgs> Scores { get; set; } = [];
}

public class ScoreArgs
{
    [JsonPropertyName("accuracy")] public double Accuracy { get; set; }
    [JsonPropertyName("ngeki")]    public int HitGeki { get; set; }
    [JsonPropertyName("n300")]     public int Hit300 { get; set; }
    [JsonPropertyName("nkatu")]    public int HitKatu { get; set; }
    [JsonPropertyName("n100")]     public int Hit100 { get; set; }
    [JsonPropertyName("n50")]      public int Hit50 { get; set; }
    [JsonPropertyName("nmiss")]    public int HitMiss { get; set; }
}
