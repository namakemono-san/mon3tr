using Calculator.Common;
using osu.Game.Scoring;
using osu.Game.Rulesets;
using osu.Game.Rulesets.Mods;
using osu.Game.Rulesets.Catch;
using osu.Game.Rulesets.Mania;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Taiko;
using osu.Game.Rulesets.Scoring;
using Calculator.Models;
using osu.Game.Rulesets.Osu.Mods;

namespace Calculator.Services;

public class OsuPpCalculator : IPpCalculator
{
    private static Ruleset GetRuleset(int mode) => mode switch
    {
        0 => new OsuRuleset(),
        1 => new TaikoRuleset(),
        2 => new CatchRuleset(),
        3 => new ManiaRuleset(),
        _ => throw new ArgumentException("Invalid ruleset ID provided.")
    };

    private static Mod[] GetMods(Ruleset ruleset, int modsBitmask)
    {
        var bitToAcr = new Dictionary<int, string>
        {
            { 1, "NF" }, { 2, "EZ" }, { 8, "HD" }, { 16, "HR" }, { 32, "SD" },
            { 64, "DT" }, { 128, "RX" }, { 256, "HT" }, { 512, "NC" }, { 1024, "FL" },
            { 2048, "Autoplay" }, { 8192, "Relax2" }, { 16384, "PF" }
        };

        var acrs = new List<string>(8);
        acrs.AddRange(from kv in bitToAcr where (modsBitmask & kv.Key) != 0 select kv.Value);

        if (acrs.Contains("RX"))
            acrs.Remove("RX");
        
        if (acrs.Contains("Relax2"))
            acrs.Remove("Relax2");
        
        if (acrs.Contains("NC") && acrs.Contains("DT"))
            acrs.Remove("DT");

        var all = ruleset.CreateAllMods();
        IEnumerable<Mod> mods = [];
        if (acrs.Count > 0)
        {
            mods = acrs.Select(acr => all.FirstOrDefault(x => string.Equals(x.Acronym, acr, StringComparison.OrdinalIgnoreCase))).OfType<Mod>().Aggregate(mods, (current, m) => current.Append(m));
        }

        mods = mods.Append(new OsuModClassic());

        return mods
            .GroupBy(m => m.GetType())
            .Select(g => g.First())
            .ToArray();
    }

    private static Dictionary<HitResult, int> GenerateHitResult(ScoreArgs a, int mode) => mode switch
    {
        0 => new Dictionary<HitResult, int>
        {
            { HitResult.Great, a.Hit300 },
            { HitResult.Ok, a.Hit100 },
            { HitResult.Meh, a.Hit50 },
            { HitResult.Miss, a.HitMiss }
        },
        1 => new Dictionary<HitResult, int>
        {
            { HitResult.Great, a.Hit300 },
            { HitResult.Ok, a.Hit100 },
            { HitResult.Miss, a.HitMiss }
        },
        2 => new Dictionary<HitResult, int>
        {
            { HitResult.Great, a.Hit300 },
            { HitResult.LargeTickHit, a.Hit100 },
            { HitResult.SmallTickHit, a.Hit50 },
            { HitResult.SmallTickMiss, a.HitKatu },
            { HitResult.Miss, a.HitMiss }
        },
        3 => new Dictionary<HitResult, int>
        {
            { HitResult.Perfect, a.HitGeki },
            { HitResult.Great, a.Hit300 },
            { HitResult.Good, a.HitKatu },
            { HitResult.Ok, a.Hit100 },
            { HitResult.Meh, a.Hit50 },
            { HitResult.Miss, a.HitMiss }
        },
        _ => throw new ArgumentException("Invalid mode.")
    };

    private static double ExtractPp(object performanceAttributes)
    {
        var t = performanceAttributes.GetType();
        var p = t.GetProperty("Total") ?? t.GetProperty("PerformancePoints") ?? t.GetProperty("PP");
        var v = p?.GetValue(performanceAttributes);
        return v is double d ? d : 0d;
    }

    public ResultData ComputeBatch(BatchArgs args)
    {
        var ruleset = GetRuleset(args.Mode);
        var mods = GetMods(ruleset, args.Mods);
        var workingBeatmap = ProcessorWorkingBeatmap.FromFile(args.FilePath);
        var beatmap = workingBeatmap.GetPlayableBeatmap(ruleset.RulesetInfo, mods);

        var difficultyAttributes = ruleset.CreateDifficultyCalculator(workingBeatmap).Calculate(mods);
        var performanceCalculator = ruleset.CreatePerformanceCalculator()!;

        var scores = new List<ScoreOut>(args.Scores.Count);
        foreach (var score in args.Scores)
        {
            ScoreInfo scoreInfo = new(beatmap.BeatmapInfo, ruleset.RulesetInfo)
            {
                Accuracy = score.Accuracy,
                MaxCombo = args.Combo,
                Statistics = GenerateHitResult(score, args.Mode),
                Mods = mods
            };

            var performanceAttributes = performanceCalculator.Calculate(scoreInfo, difficultyAttributes);
            scores.Add(new ScoreOut
            {
                Accuracy = score.Accuracy,
                Pp = ExtractPp(performanceAttributes)
            });
        }

        return new ResultData
        {
            Stars = difficultyAttributes.StarRating,
            Scores = scores
        };
    }
}
