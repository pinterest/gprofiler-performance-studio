//
// Copyright (C) 2023 Intel Corporation
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//    http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//

package main

import (
	"flag"
	"strings"
	"time"
)

type CLIArgs struct {
	SQSQueue                   string
	S3Bucket                   string
	S3PathPrefix               string
	ClickHouseAddr             string
	ClickHouseUser             string
	ClickHousePassword         string
	ClickHouseUseTLS           bool
	ClickHouseStacksTable      string
	ClickHouseMetricsTable     string
	Concurrency                int
	ClickHouseStacksBatchSize  int
	ClickHouseMetricsBatchSize int
	InputFolder                string
	FrameReplaceFileName       string
	AWSEndpoint                string
	AWSRegion                  string
	// Metrics Publisher Configuration
	MetricsEnabled     bool
	MetricsAgentURL    string
	MetricsServiceName string
	MetricsSLIUUID     string
	// PostgreSQL Configuration
	PostgresHost     string
	PostgresPort     int
	PostgresUser     string
	PostgresPassword string
	PostgresDB       string
	// Trace reports (disabled unless TraceReportsConfig is set)
	TraceReportsConfig        string
	TraceReportsInterval      time.Duration
	TraceReportsTimeout       time.Duration
	TraceReportsS3Prefix      string
	TraceReportsRetentionDays int
	TraceReportsScript        string
	TraceReportsPython        string
}

func NewCliArgs() *CLIArgs {
	return &CLIArgs{
		ClickHouseAddr:             "localhost:9000",
		ClickHouseStacksTable:      "flamedb.samples",
		ClickHouseMetricsTable:     "flamedb.metrics",
		ClickHouseUser:             "default",
		ClickHousePassword:         "",
		ClickHouseUseTLS:           false,
		Concurrency:                2,
		ClickHouseStacksBatchSize:  10000,
		ClickHouseMetricsBatchSize: 100,
		FrameReplaceFileName:       ConfPrefix + "replace.yaml",
		// Metrics defaults
		MetricsEnabled:     false,
		MetricsAgentURL:    "tcp://localhost:18126",
		MetricsServiceName: "gprofiler-indexer",
		MetricsSLIUUID:     "",
		// PostgreSQL defaults
		PostgresHost:     "localhost",
		PostgresPort:     5432,
		PostgresUser:     "gprofiler",
		PostgresPassword: "",
		PostgresDB:       "gprofiler_db",
		// Trace reports defaults
		TraceReportsInterval:      time.Hour,
		TraceReportsTimeout:       45 * time.Minute,
		TraceReportsS3Prefix:      "trace_reports",
		TraceReportsRetentionDays: 14,
		TraceReportsScript:        "/trace_reports/trace_flame.py",
		TraceReportsPython:        "python3",
	}
}

func (ca *CLIArgs) ParseArgs() {
	flag.StringVar(&ca.SQSQueue, "sqs-queue", LookupEnvOrString("SQS_QUEUE_URL", ca.SQSQueue),
		"SQS Queue name to listen")
	flag.StringVar(&ca.S3Bucket, "s3-bucket", LookupEnvOrString("S3_BUCKET", ca.S3Bucket), "S3 bucket name")
	flag.StringVar(&ca.S3PathPrefix, "s3-path-prefix", LookupEnvOrString("S3_PATH_PREFIX", ca.S3PathPrefix), "optional S3 key prefix (before products/)")
	flag.StringVar(&ca.AWSEndpoint, "aws-endpoint", LookupEnvOrString("AWS_ENDPOINT_URL", ca.AWSEndpoint), "AWS Endpoint URL")
	flag.StringVar(&ca.AWSRegion, "aws-region", LookupEnvOrString("AWS_REGION", ca.AWSRegion), "AWS Region")
	flag.StringVar(&ca.ClickHouseAddr, "clickhouse-addr", LookupEnvOrString("CLICKHOUSE_ADDR", ca.ClickHouseAddr),
		"ClickHouse address like 127.0.0.1:9000")
	flag.StringVar(&ca.ClickHouseUser, "clickhouse-user", LookupEnvOrString("CLICKHOUSE_USER", ca.ClickHouseUser),
		"ClickHouse user (default default)")
	flag.StringVar(&ca.ClickHousePassword, "clickhouse-password", LookupEnvOrString("CLICKHOUSE_PASSWORD",
		ca.ClickHousePassword), "ClickHouse password (default empty)")
	flag.BoolVar(&ca.ClickHouseUseTLS, "clickhouse-use-tls", LookupEnvOrBool("CLICKHOUSE_USE_TLS",
		ca.ClickHouseUseTLS), "ClickHouse use TLS (default false)")
	flag.StringVar(&ca.ClickHouseStacksTable, "clickhouse-stacks-table", LookupEnvOrString("CLICKHOUSE_STACKS_TABLE",
		ca.ClickHouseStacksTable), "ClickHouse stacks table (default samples)")
	flag.StringVar(&ca.InputFolder, "input-folder", "", "process files in local folder instead of listen SQS ("+
		"only for developers)")
	flag.StringVar(&ca.ClickHouseMetricsTable, "clickhouse-metrics-table", LookupEnvOrString("CLICKHOUSE_METRICS_TABLE",
		ca.ClickHouseMetricsTable), "ClickHouse metrics table (default metrics)")
	flag.IntVar(&ca.Concurrency, "c", LookupEnvOrInt("CONCURRENCY", ca.Concurrency), "Concurrency")
	flag.IntVar(&ca.ClickHouseStacksBatchSize, "clickhouse-stacks-batch-size",
		LookupEnvOrInt("CLICKHOUSE_STACKS_BATCH_SIZE", ca.ClickHouseStacksBatchSize),
		"clickhouse stack batch size (default 10000)")
	flag.IntVar(&ca.ClickHouseMetricsBatchSize, "clickhouse-metrics-batch-size",
		LookupEnvOrInt("CLICKHOUSE_METRICS_BATCH_SIZE", ca.ClickHouseMetricsBatchSize),
		"clickhouse metrics batch size (default 100)")
	flag.StringVar(&ca.FrameReplaceFileName, "replace-file", LookupEnvOrString("REPLACE_FILE",
		ca.FrameReplaceFileName),
		"replace.yaml")
	// Metrics Publisher Configuration
	flag.BoolVar(&ca.MetricsEnabled, "metrics-enabled", LookupEnvOrBool("METRICS_ENABLED", ca.MetricsEnabled),
		"Enable metrics publishing (default false)")
	flag.StringVar(&ca.MetricsAgentURL, "metrics-agent-url", LookupEnvOrString("METRICS_AGENT_URL", ca.MetricsAgentURL),
		"Metrics agent URL (default tcp://localhost:18126)")
	flag.StringVar(&ca.MetricsServiceName, "metrics-service-name", LookupEnvOrString("METRICS_SERVICE_NAME", ca.MetricsServiceName),
		"Service name for metrics (default gprofiler-indexer)")
	flag.StringVar(&ca.MetricsSLIUUID, "metrics-sli-uuid", LookupEnvOrString("METRICS_SLI_UUID", ca.MetricsSLIUUID),
		"SLI metric UUID")
	// PostgreSQL Configuration (using same env vars as webapp for consistency)
	flag.StringVar(&ca.PostgresHost, "postgres-host", LookupEnvOrString("GPROFILER_POSTGRES_HOST", ca.PostgresHost),
		"PostgreSQL host (default localhost)")
	flag.IntVar(&ca.PostgresPort, "postgres-port", LookupEnvOrInt("GPROFILER_POSTGRES_PORT", ca.PostgresPort),
		"PostgreSQL port (default 5432)")
	flag.StringVar(&ca.PostgresUser, "postgres-user", LookupEnvOrString("GPROFILER_POSTGRES_USERNAME", ca.PostgresUser),
		"PostgreSQL user (default gprofiler)")
	flag.StringVar(&ca.PostgresPassword, "postgres-password", LookupEnvOrString("GPROFILER_POSTGRES_PASSWORD", ca.PostgresPassword),
		"PostgreSQL password")
	flag.StringVar(&ca.PostgresDB, "postgres-db", LookupEnvOrString("GPROFILER_POSTGRES_DB_NAME", ca.PostgresDB),
		"PostgreSQL database name (default gprofiler_db)")
	// Trace reports
	flag.StringVar(&ca.TraceReportsConfig, "trace-reports-config", LookupEnvOrString("TRACE_REPORTS_CONFIG",
		ca.TraceReportsConfig), "trace report config JSON; enables hourly trace reports when set")
	flag.DurationVar(&ca.TraceReportsInterval, "trace-reports-interval", LookupEnvOrDuration("TRACE_REPORTS_INTERVAL",
		ca.TraceReportsInterval), "how often trace reports are rendered (default 1h)")
	flag.DurationVar(&ca.TraceReportsTimeout, "trace-reports-timeout", LookupEnvOrDuration("TRACE_REPORTS_TIMEOUT",
		ca.TraceReportsTimeout), "time limit for one trace reports run (default 45m)")
	flag.StringVar(&ca.TraceReportsS3Prefix, "trace-reports-s3-prefix", LookupEnvOrString("TRACE_REPORTS_S3_PREFIX",
		ca.TraceReportsS3Prefix), "S3 key prefix for trace reports, after -s3-path-prefix (default trace_reports)")
	flag.IntVar(&ca.TraceReportsRetentionDays, "trace-reports-retention-days", LookupEnvOrInt("TRACE_REPORTS_RETENTION_DAYS",
		ca.TraceReportsRetentionDays), "days to keep trace reports (default 14)")
	flag.StringVar(&ca.TraceReportsScript, "trace-reports-script", LookupEnvOrString("TRACE_REPORTS_SCRIPT",
		ca.TraceReportsScript), "trace report generator script")
	flag.StringVar(&ca.TraceReportsPython, "trace-reports-python", LookupEnvOrString("TRACE_REPORTS_PYTHON",
		ca.TraceReportsPython), "python interpreter for the generator")
	flag.Parse()

	// Normalize: strip any leading/trailing slashes so concatenation is consistent
	ca.S3PathPrefix = strings.Trim(ca.S3PathPrefix, "/")
	ca.TraceReportsS3Prefix = strings.Trim(ca.TraceReportsS3Prefix, "/")
	if ca.TraceReportsConfig != "" && (ca.TraceReportsS3Prefix == "" || ca.TraceReportsInterval < time.Minute) {
		logger.Fatal("trace reports need a non-empty -trace-reports-s3-prefix and an interval of at least 1m")
	}

	if ca.SQSQueue == "" && ca.InputFolder == "" {
		logger.Fatal("You must supply the name of a queue (-sqs-queue QUEUE)")
	}

	if ca.S3Bucket == "" && ca.InputFolder == "" {
		logger.Fatal("You must supply the name of a bucket (-s3-bucket BUCKET)")
	}
}
