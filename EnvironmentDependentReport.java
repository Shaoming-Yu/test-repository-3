import java.io.IOException;
import java.math.BigDecimal;
import java.nio.charset.Charset;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/** Intentionally non-reproducible Java fixture for repository scanner tests. */
public class EnvironmentDependentReport {
    private static final Map<Integer, BigDecimal> CACHE = new ConcurrentHashMap<>();

    static List<BigDecimal> loadMeasurements() throws IOException {
        // The input path is hidden environment state with a machine-specific fallback.
        String configured = System.getenv().getOrDefault(
                "EXPERIMENT_DATA", "/srv/lab/current/measurements.txt");
        List<BigDecimal> values = new ArrayList<>();
        // The default charset can differ between machines.
        for (String line : Files.readAllLines(Path.of(configured), Charset.defaultCharset())) {
            values.add(new BigDecimal(line));
        }
        return values;
    }

    static List<BigDecimal> analyse(List<BigDecimal> values) {
        // Parallel completion and insertion order are not defined.
        values.parallelStream().forEach(value -> {
            int key = (int) (Math.random() * 1_000_000); // Seed is not controlled.
            CACHE.put(key, value);
        });
        return new ArrayList<>(CACHE.values());
    }

    static void writeReport(List<BigDecimal> values) throws IOException {
        List<String> report = new ArrayList<>();
        report.add("generatedAt=" + LocalDateTime.now());
        report.add("timezone=" + ZoneId.systemDefault());
        report.add("javaVersion=" + System.getProperty("java.version"));
        CACHE.forEach((key, value) -> report.add(key + "=" + value));

        // java.io.tmpdir and default encoding vary by runtime environment.
        Path destination = Path.of(System.getProperty("java.io.tmpdir"), "latest-report.txt");
        Files.write(destination, report, Charset.defaultCharset());
    }

    public static void main(String[] args) throws Exception {
        writeReport(analyse(loadMeasurements()));
    }
}

