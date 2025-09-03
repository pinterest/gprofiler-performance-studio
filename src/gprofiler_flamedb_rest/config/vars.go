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

package config

var (
	ClickHouseAddr         = "localhost:9000"
	ClickHouseStacksTable  = "flamedb.samples"
	ClickHouseMetricsTable = "flamedb.metrics"
	UseTLS                 = true
	CertFilePath           = ""
	KeyFilePath            = ""
	Credentials            = "user:password"
	
	// Data retention periods (in days)
	RawRetentionDays     = 7   // Raw data retention period
	MinuteRetentionDays  = 30  // Minute aggregation retention period
	HourlyRetentionDays  = 90  // Hourly aggregation retention period
	DailyRetentionDays   = 365 // Daily aggregation retention period
)
