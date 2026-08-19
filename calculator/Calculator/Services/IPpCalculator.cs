using Calculator.Models;

namespace Calculator.Services;

public interface IPpCalculator
{
    ResultData ComputeBatch(BatchArgs args);
}