# gProfiler Performance Studio - Table Selection Logic

## Overview

The gProfiler Performance Studio backend uses a sophisticated table selection mechanism to efficiently query profiling data from ClickHouse. This document explains how the system chooses which tables to query based on data age, time ranges, and resolution requirements.

## ClickHouse Table Structure

The system maintains multiple aggregation levels for optimal query performance:

```
┌─────────────────┬─────────────────┬─────────────────┬─────────────────┐
│   Raw Tables    │  Minute Tables  │  Hourly Tables  │  Daily Tables   │
├─────────────────┼─────────────────┼─────────────────┼─────────────────┤
│ samples         │ samples_1min    │ samples_1hour   │ samples_1day    │
│ (highest        │ (1-minute       │ (1-hour         │ (1-day          │
│  precision)     │  aggregation)   │  aggregation)   │  aggregation)   │
│                 │                 │                 │                 │
│ TTL: 7 days     │ TTL: 30 days    │ TTL: 90 days    │ TTL: 365 days   │
│ (configurable)  │ (configurable)  │ (configurable)  │ (configurable)  │
└─────────────────┴─────────────────┴─────────────────┴─────────────────┘
```

### Table Details

- **`samples`**: Raw profiling data with highest precision
- **`samples_1min`**: Minute-level aggregations (used for metadata queries)
- **`samples_1hour`**: Hour-level aggregations for medium-term data
- **`samples_1day`**: Day-level aggregations for long-term historical data

## Table Selection Logic Evolution

### 🐛 **Previous Logic (Buggy)**

The old implementation had a critical flaw that caused identical data to be returned for different time ranges:

```go
// OLD BUGGY LOGIC
retentionInterval := time.Hour * 24 * 14  // Fixed 14-day threshold
if now.Sub(start) >= retentionInterval {
    // ❌ PROBLEM: Forces day-level aggregation too early
    result["1day_historical"] = makeTimeRange(makeStartOfDay(start), makeEndOfDay(end))
    return result  // ❌ Both URLs get same day boundaries!
}
```

**Problem Example:**
```
URL 1: 2025-08-12T15:00:47Z → 2025-08-12T16:00:47Z
URL 2: 2025-08-12T16:00:24Z → 2025-08-12T17:00:24Z

Both became: 2025-08-12T00:00:00Z → 2025-08-12T23:59:59Z  (identical!)
```

### ✅ **Current Logic (Fixed)**

The new implementation properly handles retention periods and preserves time precision:

```go
// NEW FIXED LOGIC
rawRetentionInterval := time.Hour * 24 * time.Duration(config.RawRetentionDays)
hourlyRetentionInterval := time.Hour * 24 * time.Duration(config.HourlyRetentionDays)
dailyThreshold := time.Hour * 24 * time.Duration(config.HourlyRetentionDays)

// ✅ Proper retention cascade
if now.Sub(start) >= dailyThreshold {
    return daily_aggregation_with_day_boundaries()
} else if now.Sub(start) >= rawRetentionInterval && now.Sub(start) < hourlyRetentionInterval {
    return hourly_aggregation_with_exact_times()  // ✅ Preserves precision!
} else {
    return normal_logic()
}
```

## Resolution Parameter Logic

The system supports different resolution modes that affect table selection:

### Resolution Types

| Resolution | Description | Table Selection Logic |
|-----------|-------------|----------------------|
| `raw` | Highest precision data | Uses `samples` table if available, falls back to `samples_1hour` |
| `hour` | Hour-level aggregation | Uses `samples_1hour` table |
| `day` | Day-level aggregation | Uses `samples_1day` table |
| `multi` (default) | Smart selection | Uses `sliceMultiRange()` for complex time spans |

### Multi-Resolution Logic (`sliceMultiRange`)

For complex time ranges, the system intelligently splits queries across multiple tables:

```go
func sliceMultiRange(result map[string][]TimeRange, start time.Time, end time.Time) {
    // Split time range into optimal table queries:
    
    // Raw tables: For partial hours at start/end (highest precision)
    result["raw"] = append(result["raw"], makeTimeRange(start, endOfHour))
    result["raw"] = append(result["raw"], makeTimeRange(startOfHour, end))
    
    // Hourly tables: For full hours within days
    if end.After(endOfDay) {
        result["1hour"] = append(result["1hour"], makeTimeRange(endOfHour, endOfDay))
        result["1hour"] = append(result["1hour"], makeTimeRange(startOfDay, previousHour))
    }
    
    // Daily tables: For full days in very long ranges
    if !previousDay.Equal(endOfDay) {
        result["1day"] = append(result["1day"], makeTimeRange(endOfDay, previousDay))
    }
}
```

## Configurable Retention Periods

### Environment Variables

The retention logic is now fully configurable via environment variables:

```bash
# Raw data retention (default: 7 days)
RAW_RETENTION_DAYS=7

# Minute aggregation retention (default: 30 days)
MINUTE_RETENTION_DAYS=30

# Hourly aggregation retention (default: 90 days)
HOURLY_RETENTION_DAYS=90

# Daily aggregation retention (default: 365 days)
DAILY_RETENTION_DAYS=365
```

### Command Line Flags

```bash
./gprofiler_flamedb_rest \
  --raw-retention-days=7 \
  --minute-retention-days=30 \
  --hourly-retention-days=90 \
  --daily-retention-days=365
```

## Query Examples

### Example 1: Recent Data (< 7 days old)

```
Query: 2025-08-25T15:00:00Z → 2025-08-25T16:00:00Z
Age: 2 days
Resolution: multi (default)

Table Selection: samples (raw table)
SQL: SELECT ... FROM samples WHERE ... (Timestamp BETWEEN '2025-08-25 15:00:00' AND '2025-08-25 16:00:00')
```

### Example 2: Medium-Age Data (7-90 days old)

```
Query: 2025-08-12T15:00:47Z → 2025-08-12T16:00:47Z
Age: 15 days
Resolution: multi (default)

Table Selection: samples_1hour (hourly table)
SQL: SELECT ... FROM samples_1hour WHERE ... (Timestamp BETWEEN '2025-08-12 15:00:47' AND '2025-08-12 16:00:47')
```

### Example 3: Old Data (> 90 days old)

```
Query: 2025-05-12T15:00:00Z → 2025-05-12T16:00:00Z
Age: 100 days
Resolution: multi (default)

Table Selection: samples_1day (daily table with day boundaries)
SQL: SELECT ... FROM samples_1day WHERE ... (Timestamp BETWEEN '2025-05-12 00:00:00' AND '2025-05-12 23:59:59')
```

### Example 4: Long Time Range (Multi-table)

```
Query: 2025-08-12T10:00:00Z → 2025-08-13T11:00:00Z (25 hours)
Age: 15 days
Resolution: multi (default)

Table Selection: Multiple tables via sliceMultiRange()
- samples_1hour: For 2025-08-12T10:00:00Z → 2025-08-12T23:59:59Z
- samples_1hour: For 2025-08-13T00:00:00Z → 2025-08-13T11:00:00Z
```

## Troubleshooting Guide

### Problem: Identical Data for Different Time Ranges

**Symptoms:**
- Different URLs return identical flamegraphs
- Sample counts are identical despite different time ranges

**Root Cause:**
- Old retention logic forcing day-level aggregation
- Time parameter parsing issues

**Solution:**
- Ensure retention periods are configured correctly
- Verify time parameters are being parsed properly
- Check that data age falls within hourly retention period

### Problem: No Data Returned

**Symptoms:**
- Empty flamegraphs for valid time ranges
- Zero sample counts

**Root Cause:**
- Data older than configured retention periods
- Incorrect table selection logic

**Solution:**
- Check data age against retention configuration
- Verify ClickHouse TTL settings match retention config
- Use appropriate resolution parameter

### Problem: Performance Issues

**Symptoms:**
- Slow query responses
- High memory usage

**Root Cause:**
- Querying raw tables for very old data
- Inefficient table selection

**Solution:**
- Adjust retention periods to use aggregated tables sooner
- Use explicit resolution parameters for better control
- Monitor query patterns and optimize retention thresholds

## Configuration Best Practices

### Development Environment
```bash
export RAW_RETENTION_DAYS=1
export HOURLY_RETENTION_DAYS=7
export DAILY_RETENTION_DAYS=30
```

### Production Environment
```bash
export RAW_RETENTION_DAYS=7
export MINUTE_RETENTION_DAYS=30
export HOURLY_RETENTION_DAYS=90
export DAILY_RETENTION_DAYS=365
```

### High-Volume Environment
```bash
export RAW_RETENTION_DAYS=3
export MINUTE_RETENTION_DAYS=14
export HOURLY_RETENTION_DAYS=60
export DAILY_RETENTION_DAYS=180
```

## Implementation Details

### Key Files

- **`db/clickhouse.go`**: Main table selection logic in `GetTimeRanges()`
- **`config/vars.go`**: Retention period configuration variables
- **`main.go`**: Environment variable parsing and setup
- **`common/params.go`**: Query parameter definitions

### Key Functions

- **`GetTimeRanges()`**: Core table selection logic
- **`sliceMultiRange()`**: Multi-table query optimization
- **`getTableName()`**: Table name resolution
- **`CheckTimeRange()`**: Parameter validation and defaults

## Future Improvements

### Potential Enhancements

1. **Dynamic Retention**: Adjust retention based on data volume
2. **Query Optimization**: Automatic query plan optimization
3. **Monitoring**: Table selection metrics and alerts
4. **Caching**: Cache table selection decisions for repeated queries

### Monitoring Recommendations

1. Track query performance by table type
2. Monitor retention period effectiveness
3. Alert on unexpected table selection patterns
4. Measure data freshness vs. query performance trade-offs

---

## Changelog

### v1.1.0 (Current)
- ✅ Fixed retention logic to prevent identical results
- ✅ Added configurable retention periods
- ✅ Improved hour-level precision for medium-age data
- ✅ Enhanced multi-table query optimization

### v1.0.0 (Previous)
- ❌ Fixed 14-day retention threshold
- ❌ Day-boundary rounding for older data
- ❌ Limited configuration options
