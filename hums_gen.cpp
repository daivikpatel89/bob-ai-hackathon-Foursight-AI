// =============================================================================
// hums_gen.cpp — HUMS Sensor Data Generator for Military Vehicles
//
// Generates a large CSV of fake Health and Usage Monitoring System (HUMS)
// sensor data. Each vehicle degrades exponentially over time; readings are
// derived from a health_factor with Gaussian noise, and a discrete status
// (NOMINAL / WARNING / FAULT) is derived from fixed thresholds.
//
// Build:
//   g++ -std=c++17 -O2 -o hums_gen hums_gen.cpp
//   clang++ -std=c++17 -O2 -o hums_gen hums_gen.cpp
//
// Usage:
//   ./hums_gen --rows <N> --vehicles <V> --output <path> [--seed <S>]
// =============================================================================

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

// =============================================================================
// Section 1 — Configuration & CLI Parsing
// =============================================================================

struct Config {
    unsigned long long rows        = 0;
    unsigned long long vehicles    = 0;
    std::string        output_path;
    uint_fast32_t      seed        = 0;
    bool               seed_provided = false;
};

static void print_usage(const char* prog) {
    std::cerr
        << "Usage: " << prog
        << " --rows <N> --vehicles <V> --output <path> [--seed <S>]\n\n"
        << "  --rows      Total number of CSV data rows to generate  (>= 1)\n"
        << "  --vehicles  Number of unique vehicles to simulate      (>= 1)\n"
        << "  --output    Output CSV file path\n"
        << "  --seed      Optional PRNG seed for reproducible output\n";
}

static Config parse_args(int argc, char** argv) {
    Config cfg;
    for (int i = 1; i < argc; ++i) {
        std::string_view flag(argv[i]);

        auto require_next = [&](std::string_view name) -> std::string_view {
            if (i + 1 >= argc) {
                std::cerr << "Error: " << name << " requires a value.\n";
                print_usage(argv[0]);
                std::exit(EXIT_FAILURE);
            }
            return argv[++i];
        };

        if (flag == "--rows") {
            try { cfg.rows = std::stoull(std::string(require_next("--rows"))); }
            catch (...) { std::cerr << "Error: --rows must be a positive integer.\n"; std::exit(EXIT_FAILURE); }
        } else if (flag == "--vehicles") {
            try { cfg.vehicles = std::stoull(std::string(require_next("--vehicles"))); }
            catch (...) { std::cerr << "Error: --vehicles must be a positive integer.\n"; std::exit(EXIT_FAILURE); }
        } else if (flag == "--output") {
            cfg.output_path = std::string(require_next("--output"));
        } else if (flag == "--seed") {
            try {
                cfg.seed = static_cast<uint_fast32_t>(std::stoul(std::string(require_next("--seed"))));
                cfg.seed_provided = true;
            } catch (...) { std::cerr << "Error: --seed must be an unsigned integer.\n"; std::exit(EXIT_FAILURE); }
        } else {
            std::cerr << "Error: unknown argument '" << flag << "'.\n";
            print_usage(argv[0]);
            std::exit(EXIT_FAILURE);
        }
    }

    bool bad = false;
    if (cfg.rows < 1)           { std::cerr << "Error: --rows must be >= 1.\n";     bad = true; }
    if (cfg.vehicles < 1)       { std::cerr << "Error: --vehicles must be >= 1.\n"; bad = true; }
    if (cfg.output_path.empty()){ std::cerr << "Error: --output is required.\n";    bad = true; }
    if (bad) { print_usage(argv[0]); std::exit(EXIT_FAILURE); }

    return cfg;
}

// =============================================================================
// Section 2 — Per-Vehicle State Model
// =============================================================================

// Health factor range at initialisation (slight variance so vehicles don't
// degrade in lockstep)
constexpr double HEALTH_INIT_MIN = 0.95;
constexpr double HEALTH_INIT_MAX = 1.00;

// Decay rate range per tick: values close to 1.0 keep vehicles alive for tens
// of thousands of rows before hitting FAULT territory.
constexpr double DECAY_MIN = 0.99990;
constexpr double DECAY_MAX = 0.99999;

// Run-hours increment per tick (minutes converted to fractional hours)
constexpr double RUN_HOURS_DELTA_MIN = 0.005;   // ~18 seconds
constexpr double RUN_HOURS_DELTA_MAX = 0.083;   // ~5 minutes

// Timestamp jitter per tick (seconds between samples)
constexpr int TIMESTAMP_DELTA_MIN_S = 10;
constexpr int TIMESTAMP_DELTA_MAX_S = 300;

struct VehicleState {
    std::string    vehicle_id;
    double         health_factor;
    double         decay_rate;
    double         run_hours;
    std::time_t    last_timestamp;
};

static std::vector<VehicleState> init_vehicles(unsigned long long count,
                                                std::mt19937& rng) {
    std::uniform_real_distribution<double> health_dist(HEALTH_INIT_MIN, HEALTH_INIT_MAX);
    std::uniform_real_distribution<double> decay_dist (DECAY_MIN,       DECAY_MAX);
    std::uniform_real_distribution<double> hours_dist (0.0,             500.0);

    // Spread starting timestamps across the last ~180 days so vehicles don't
    // all share the same clock.
    const std::time_t now = std::time(nullptr);
    std::uniform_int_distribution<long> ts_spread(0, 180LL * 24 * 3600);

    std::vector<VehicleState> vehicles;
    vehicles.reserve(static_cast<std::size_t>(count));

    for (unsigned long long i = 0; i < count; ++i) {
        std::ostringstream id;
        id << "VH-" << std::setfill('0') << std::setw(4) << (i + 1);

        VehicleState vs;
        vs.vehicle_id     = id.str();
        vs.health_factor  = health_dist(rng);
        vs.decay_rate     = decay_dist(rng);
        vs.run_hours      = hours_dist(rng);
        vs.last_timestamp = now - ts_spread(rng);

        vehicles.push_back(std::move(vs));
    }
    return vehicles;
}

static void advance(VehicleState& vs, std::mt19937& rng) {
    // Exponential health decay
    vs.health_factor *= vs.decay_rate;

    // Irregular run-hour increment
    std::uniform_real_distribution<double> rh_delta(RUN_HOURS_DELTA_MIN, RUN_HOURS_DELTA_MAX);
    vs.run_hours += rh_delta(rng);

    // Irregular timestamp advance
    std::uniform_int_distribution<int> ts_delta(TIMESTAMP_DELTA_MIN_S, TIMESTAMP_DELTA_MAX_S);
    vs.last_timestamp += ts_delta(rng);
}

// =============================================================================
// Section 3 — Sensor Value Computation
// =============================================================================

constexpr double BASE_TEMP_C          =  75.0;  // °C at full health
constexpr double TEMP_RANGE_C         =  80.0;  // additional °C at zero health
constexpr double TEMP_NOISE_STDDEV    =   2.5;  // °C Gaussian noise

constexpr double BASE_VIBRATION       =   1.5;  // mm/s at full health
constexpr double VIBRATION_RANGE      =  18.5;  // additional mm/s at zero health
constexpr double VIBRATION_NOISE_STDDEV =  0.4; // mm/s Gaussian noise

struct SensorReading {
    double temp;       // engine_temp_c
    double vibration;  // vibration_mm_s
};

static SensorReading compute_sensors(const VehicleState& vs, std::mt19937& rng) {
    const double wear = 1.0 - vs.health_factor;   // 0 at new, grows toward 1

    std::normal_distribution<double> temp_noise(0.0, TEMP_NOISE_STDDEV);
    std::normal_distribution<double> vib_noise (0.0, VIBRATION_NOISE_STDDEV);

    SensorReading sr;
    sr.temp      = std::max(0.0, BASE_TEMP_C       + wear * TEMP_RANGE_C       + temp_noise(rng));
    sr.vibration = std::max(0.0, BASE_VIBRATION    + wear * VIBRATION_RANGE    + vib_noise(rng));
    return sr;
}

// =============================================================================
// Section 4 — Status Derivation
// =============================================================================

// Warning thresholds — sensor values above these trigger WARNING
constexpr double WARN_TEMP_C      = 105.0;   // °C
constexpr double WARN_VIBRATION   =   8.0;   // mm/s

// Fault thresholds — sensor values above these trigger FAULT
constexpr double FAULT_TEMP_C     = 120.0;   // °C
constexpr double FAULT_VIBRATION  =  14.0;   // mm/s

enum class Status { NOMINAL, WARNING, FAULT };

static std::string_view status_to_sv(Status s) {
    switch (s) {
        case Status::NOMINAL:  return "NOMINAL";
        case Status::WARNING:  return "WARNING";
        case Status::FAULT:    return "FAULT";
    }
    return "NOMINAL"; // unreachable
}

static Status derive_status(const SensorReading& sr) {
    if (sr.temp >= FAULT_TEMP_C || sr.vibration >= FAULT_VIBRATION)
        return Status::FAULT;
    if (sr.temp >= WARN_TEMP_C  || sr.vibration >= WARN_VIBRATION)
        return Status::WARNING;
    return Status::NOMINAL;
}

// =============================================================================
// Section 5 — CSV Writer & Main Loop
// =============================================================================

static std::string format_timestamp(std::time_t t) {
    char buf[20]; // "YYYY-MM-DD HH:MM:SS\0"
    std::tm* tm_info = std::localtime(&t);
    std::strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", tm_info);
    return buf;
}

int main(int argc, char** argv) {
    // ---- 1. Parse CLI ----------------------------------------------------------
    const Config cfg = parse_args(argc, argv);

    // ---- 2. Seed PRNG ----------------------------------------------------------
    std::mt19937 rng;
    if (cfg.seed_provided) {
        rng.seed(cfg.seed);
    } else {
        std::random_device rd;
        rng.seed(rd());
    }

    // ---- 3. Initialise vehicles ------------------------------------------------
    std::vector<VehicleState> vehicles = init_vehicles(cfg.vehicles, rng);

    // ---- 4. Open output file ---------------------------------------------------
    std::ofstream out(cfg.output_path);
    if (!out.is_open()) {
        std::cerr << "Error: cannot open output file '" << cfg.output_path << "'.\n";
        return EXIT_FAILURE;
    }

    // ---- 5. Write CSV header ---------------------------------------------------
    out << "timestamp,vehicle_id,engine_temp_c,vibration_mm_s,run_hours,status\n";

    // ---- 6. Main generation loop -----------------------------------------------
    out << std::fixed << std::setprecision(2);

    constexpr unsigned long long PROGRESS_INTERVAL = 1'000'000ULL;

    for (unsigned long long row = 0; row < cfg.rows; ++row) {
        // Round-robin vehicle selection
        VehicleState& vs = vehicles[static_cast<std::size_t>(row % cfg.vehicles)];

        // Advance state (timestamp, run_hours, health_factor)
        advance(vs, rng);

        // Compute sensor values from current health
        const SensorReading sr = compute_sensors(vs, rng);

        // Derive operational status
        const Status status = derive_status(sr);

        // Format and write row
        out << format_timestamp(vs.last_timestamp) << ','
            << vs.vehicle_id                        << ','
            << sr.temp                              << ','
            << sr.vibration                         << ','
            << vs.run_hours                         << ','
            << status_to_sv(status)                 << '\n';

        // Progress heartbeat on stderr
        if ((row + 1) % PROGRESS_INTERVAL == 0) {
            std::cerr << "[hums_gen] " << (row + 1) << " rows written...\n";
        }
    }

    out.flush();
    out.close();

    std::cerr << "[hums_gen] Done. " << cfg.rows << " rows written to '"
              << cfg.output_path << "'.\n";

    return EXIT_SUCCESS;
}
