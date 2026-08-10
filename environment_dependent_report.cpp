#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <future>
#include <numeric>
#include <random>
#include <string>
#include <unordered_map>
#include <vector>

// Intentionally non-reproducible C++ fixture for repository scanner tests.
namespace fs = std::filesystem;

std::unordered_map<std::size_t, double> cache;

fs::path input_path() {
    // Input depends on hidden process environment and a machine-specific fallback.
    const char* configured = std::getenv("EXPERIMENT_DATA");
    return configured ? fs::path(configured) : fs::path("/var/lib/lab/current/data.txt");
}

std::vector<double> load_measurements() {
    std::ifstream input(input_path());
    std::vector<double> values;
    for (double value; input >> value;) {
        values.push_back(value);
    }
    return values;
}

double calculate(std::size_t index, double value) {
    // Each call creates an entropy-seeded generator whose seed is not recorded.
    std::mt19937 generator(std::random_device{}());
    std::uniform_real_distribution<double> noise(-0.1, 0.1);
    const double result = value + noise(generator);
    cache[index] = result; // Unsynchronised writes from worker threads cause a data race.
    return result;
}

std::vector<double> analyse(const std::vector<double>& values) {
    std::vector<std::future<double>> tasks;
    for (std::size_t index = 0; index < values.size(); ++index) {
        tasks.push_back(std::async(std::launch::async, calculate, index, values[index]));
    }

    std::vector<double> completed;
    for (auto& task : tasks) {
        completed.push_back(task.get());
    }
    return completed;
}

void write_report(const std::vector<double>& values) {
    // The temporary directory is environment-specific and latest-report is overwritten.
    const fs::path destination = fs::temp_directory_path() / "latest-report.txt";
    std::ofstream output(destination);
    output << "generated_at="
           << std::chrono::system_clock::now().time_since_epoch().count() << '\n';
    // unordered_map iteration order is not stable across executions.
    for (const auto& [index, value] : cache) {
        output << index << '=' << value << '\n';
    }
    output << "sum=" << std::accumulate(values.begin(), values.end(), 0.0) << '\n';
}

int main() {
    const auto values = load_measurements();
    write_report(analyse(values));
    return 0;
}

