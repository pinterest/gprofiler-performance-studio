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

package handlers

import (
	"fmt"
	"io"
	"log"
	"net/http"
	"strings"
	"time"

	"github.com/a8m/rql"

	"restflamedb/common"
	"restflamedb/db"

	"github.com/gin-gonic/gin"
)

type Handlers struct {
	ChClient *db.ClickHouseClient
}

var QueryParser = rql.MustNewParser(rql.Config{
	Model:         common.FiltersParams{},
	FieldSep:      ".",
	LimitMaxValue: 25,
})

var MetricsQueryParser = rql.MustNewParser(rql.Config{
	Model:         common.MetricsFiltersParams{},
	FieldSep:      ".",
	LimitMaxValue: 25,
})

func (h Handlers) GetFlamegraph(c *gin.Context) {
	params, query, err := parseParams(common.FlameGraphParams{}, QueryParser, c)
	if err != nil {
		return
	}

	start := c.GetTime("requestStartTime")
	graph, err := h.ChClient.GetTopFrames(c.Request.Context(), params, query)
	if err != nil {
		c.String(http.StatusInternalServerError, err.Error())
		return
	}
	olapTime := float64(time.Since(start)) / float64(time.Second)
	runtimes := make(map[string]float64)

	if graph.EnrichWithLang {
		runtimes = db.CalcRuntimesDistribution(&graph)
	}

	switch params.Format {
	case "flamegraph":
		total, final := graph.BuildFlameGraph()
		percentiles := graph.GetPercentiles()

		result := FlameGraphResponse{
			Name:        "root",
			Value:       total,
			Children:    final,
			OlapTime:    olapTime,
			Percentiles: percentiles,
		}
		result.SetExecTime(start)

		c.JSON(http.StatusOK, result)
	case "collapsed_file":
		ch := make(chan string)
		go graph.BuildCollapsedFile(ch, runtimes)
		lineNum := 0
		c.Stream(func(w io.Writer) bool {
			line, more := <-ch
			if !more {
				if lineNum == 0 {
					c.Writer.WriteHeader(http.StatusNoContent)
				}
				return false
			}
			c.Writer.Write([]byte(line))
			lineNum += 1
			return true
		})
		// Read rest of data in channel if exist
		for {
			_, more := <-ch
			if !more {
				break
			}
		}
	default:
		c.String(http.StatusBadRequest, "Unknown format")
	}
}

func (h Handlers) QueryMeta(c *gin.Context) {
	var response ExecTimeInterface
	params, query, err := parseParams(common.QueryParams{}, QueryParser, c)
	if err != nil {
		return
	}

	mapping := map[string]string{
		"container":        "ContainerName",
		"hostname":         "HostName",
		"instance_type":    "InstanceType",
		"k8s_obj":          "ContainerEnvName",
		"ContainerName":    "ContainerName",
		"HostName":         "HostName",
		"InstanceType":     "InstanceType",
		"ContainerEnvName": "ContainerEnvName",
	}

	ctx := c.Request.Context()
	switch params.LookupFor {
	case "HostName", "hostname", "InstanceType", "instance_type":
		response = &FieldValueSampleResponse{
			Result: h.ChClient.FetchFieldValues(ctx, mapping[params.LookupFor], params, query),
		}
	case "ContainerEnvName", "k8s_obj", "ContainerName", "container":
		response = &FieldValueSampleResponse{
			Result: h.ChClient.FetchFieldValueSample(ctx, mapping[params.LookupFor], params, query),
		}

	case "instance_type_count":
		response = &InstanceTypeCountResponse{
			Result: h.ChClient.FetchInstanceTypeCount(ctx, params, query),
		}

	case "time":
		response = &QueryResponse{
			Result: h.ChClient.FetchTimes(ctx, params, query),
		}
	case "time_range":
		response = &QueryResponse{
			Result: h.ChClient.FetchTimeRange(ctx, params, query),
		}
	case "samples":
		response = &SampleCountResponse{
			Result: h.ChClient.FetchSampleCount(ctx, params, query),
		}
	case "samples_count_by_function":
		if len(params.FunctionName) > 0 {
			response = &SampleCountByFunctionResponse{
				Result: h.ChClient.FetchSampleCountByFunction(ctx, params, query),
			}
		} else {
			c.JSON(http.StatusBadRequest, "missing function name")
		}
	default:
		response = &QueryResponse{
			Result: make([]string, 0),
		}
	}
	response.SetExecTime(c.GetTime("requestStartTime"))
	c.JSON(http.StatusOK, response)
}

func (h Handlers) QueryServices(c *gin.Context) {
	params, _, err := parseParams(common.ServicesParams{}, nil, c)
	if err != nil {
		return
	}

	ctx := c.Request.Context()
	response := ServiceResponse{
		Result: h.ChClient.FetchServices(ctx, params),
	}
	response.SetExecTime(c.GetTime("requestStartTime"))
	c.JSON(http.StatusOK, response)
}

func (h Handlers) QuerySessionsCount(c *gin.Context) {
	params, query, err := parseParams(common.SessionsCountParams{}, QueryParser, c)
	if err != nil {
		return
	}
	ctx := c.Request.Context()
	response := SessionsResponse{}
	result, err := h.ChClient.FetchSessionsCount(ctx, params, query)
	response.SetExecTime(c.GetTime("requestStartTime"))
	if err == nil {
		response.Result = result
		c.JSON(http.StatusOK, response)
	} else {
		c.JSON(http.StatusNoContent, err)
	}
}

func (h Handlers) GetMetricsSummary(c *gin.Context) {
	params, query, err := parseParams(common.MetricsSummaryParams{}, MetricsQueryParser, c)
	if err != nil {
		return
	}
	ctx := c.Request.Context()

	if fetchResponse, err := h.ChClient.FetchMetricsSummary(ctx, params, query); err != nil {
		log.Print(err)
		c.Status(http.StatusNoContent)
		return
	} else {
		response := MetricsSummaryResponse{
			Result: fetchResponse,
		}
		response.SetExecTime(c.GetTime("requestStartTime"))
		c.JSON(http.StatusOK, response)
	}

}

func (h Handlers) GetMetricsServicesListSummary(c *gin.Context) {
	body := common.MetricsServicesListSummaryParams{}
	if err := c.ShouldBindJSON(&body); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	if err := c.ShouldBindQuery(&body); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	ctx := c.Request.Context()

	if fetchResponse, err := h.ChClient.FetchMetricsServicesListSummary(ctx, body); err != nil {
		log.Print(err)
		c.Status(http.StatusNoContent)
		return
	} else {
		response := MetricsServicesListSummaryResponse{
			Result: fetchResponse,
		}
		response.SetExecTime(c.GetTime("requestStartTime"))
		c.JSON(http.StatusOK, response)
	}

}

func (h Handlers) GetMetricsGraph(c *gin.Context) {
	params, query, err := parseParams(common.MetricsSummaryParams{}, MetricsQueryParser, c)
	if err != nil {
		return
	}
	ctx := c.Request.Context()

	if fetchResponse, err := h.ChClient.FetchMetricsGraph(ctx, params, query); err != nil {
		log.Print(err)
		c.Status(http.StatusNoContent)
		return
	} else {
		response := MetricsGraphResponse{
			Result: fetchResponse,
		}
		response.SetExecTime(c.GetTime("requestStartTime"))
		c.JSON(http.StatusOK, response)
	}
}

func (h Handlers) GetMetricsCpuTrends(c *gin.Context) {
	params, query, err := parseParams(common.MetricsCpuTrendParams{}, MetricsQueryParser, c)
	if err != nil {
		return
	}
	ctx := c.Request.Context()

	if fetchResponse, err := h.ChClient.FetchMetricsCpuTrend(ctx, params, query); err != nil {
		log.Print(err)
		c.Status(http.StatusNoContent)
		return
	} else {
		response := MetricsCpuResponse{
			Result: fetchResponse,
		}
		response.SetExecTime(c.GetTime("requestStartTime"))
		c.JSON(http.StatusOK, response)
	}
}

func (h Handlers) GetLastHTML(c *gin.Context) {
	params, query, err := parseParams(common.MetricsLastHTMLParams{}, nil, c)
	if err != nil {
		return
	}
	fmt.Println(params, query)
	ctx := c.Request.Context()
	htmlPath, err := h.ChClient.FetchLastHTML(ctx, params, query)
	if err != nil {
		return
	}
	response := MetricsHTMLResponse{
		Result: htmlPath,
	}
	response.SetExecTime(c.GetTime("requestStartTime"))
	c.JSON(http.StatusOK, response)
}

// OptimizationResponse represents the API response for optimization recommendations
type OptimizationResponse struct {
	Result []db.OptimizationRecommendation `json:"result"`
	ExecTimeResponse
}

func (h Handlers) GetOptimizationRecommendations(c *gin.Context) {
	// Parse query parameters
	serviceId := c.Query("service_id")
	namespace := c.Query("namespace") 
	technology := c.Query("technology")
	complexity := c.Query("complexity")
	optimizationType := c.Query("optimization_type")
	ruleName := c.Query("rule_name")
	minImpact := c.DefaultQuery("min_impact", "0")
	minPrecision := c.DefaultQuery("min_precision", "0")
	minHosts := c.DefaultQuery("min_hosts", "0")
	limit := c.DefaultQuery("limit", "1000")

	// Debug logging
	log.Printf("DEBUG: Received filters - serviceId='%s', namespace='%s', technology='%s', complexity='%s', optimizationType='%s', ruleName='%s', minImpact='%s', minPrecision='%s', minHosts='%s'", 
		serviceId, namespace, technology, complexity, optimizationType, ruleName, minImpact, minPrecision, minHosts)

	// Build WHERE clause (simplified - remove date filtering for now)
	whereConditions := []string{}
	
	if serviceId != "" {
		whereConditions = append(whereConditions, fmt.Sprintf("ServiceId = '%s'", serviceId)) // ServiceId is stored as string
	}
	if namespace != "" {
		whereConditions = append(whereConditions, fmt.Sprintf("namespace = '%s'", namespace))
	}
	if technology != "" {
		whereConditions = append(whereConditions, fmt.Sprintf("Technology = '%s'", technology))
	}
	if complexity != "" {
		whereConditions = append(whereConditions, fmt.Sprintf("ImplementationComplexity = '%s'", complexity))
	}
	if optimizationType != "" {
		whereConditions = append(whereConditions, fmt.Sprintf("OptimizationType = '%s'", optimizationType))
	}
	if ruleName != "" {
		whereConditions = append(whereConditions, fmt.Sprintf("RuleName ILIKE '%%%s%%'", ruleName))
	}
	if minImpact != "0" {
		whereConditions = append(whereConditions, fmt.Sprintf("RelativeResourceReductionPercentInService >= %s", minImpact))
	}
	if minPrecision != "0" {
		whereConditions = append(whereConditions, fmt.Sprintf("PrecisionScore >= %s", minPrecision))
	}
	if minHosts != "0" {
		whereConditions = append(whereConditions, fmt.Sprintf("NumHosts >= %s", minHosts))
	}
	
	whereClause := ""
	if len(whereConditions) > 0 {
		whereClause = " WHERE " + strings.Join(whereConditions, " AND ")
	}

	// Build and execute query
	query := fmt.Sprintf(`
		SELECT
			ServiceId,
			namespace,
			Technology,
			OptimizationPattern,
			ActionableRecommendation,
			ImplementationComplexity,
			RuleId,
			RuleName,
			RuleCategory,
			OptimizationType,
			RuleSource,
			toString(TopAffectedStacks) as TopAffectedStacks,
			MinGlobalImpactPercent,
			MaxGlobalImpactPercent,
			PrecisionScore,
			AccuracyScore,
			AffectedStacks,
			TotalSamplesInPattern,
			RelativeResourceReductionPercentInService,
			DollarImpact,
			NumHosts,
			toString(created_date) as created_date,
			toString(updated_date) as updated_date,
			created_by
		FROM flamedb.optimization_pattern_summary_v2_local
		%s
		ORDER BY RelativeResourceReductionPercentInService DESC, ServiceId
		LIMIT %s
	`, whereClause, limit)

	ctx := c.Request.Context()
	
	if fetchResponse, err := h.ChClient.FetchOptimizationRecommendations(ctx, query); err != nil {
		log.Printf("Error fetching optimization recommendations: %v", err)
		c.Status(http.StatusNoContent)
		return
	} else {
		response := OptimizationResponse{
			Result: fetchResponse,
		}
		response.SetExecTime(c.GetTime("requestStartTime"))
		c.JSON(http.StatusOK, response)
	}
}

func (h Handlers) GetOptimizationSummary(c *gin.Context) {
	query := `
		SELECT
			count() as total_recommendations,
			countDistinct(ServiceId) as affected_services,
			countDistinct(Technology) as technologies_count,
			sum(AffectedStacks) as total_affected_stacks,
			avg(RelativeResourceReductionPercentInService) as avg_cpu_impact,
			max(RelativeResourceReductionPercentInService) as max_cpu_impact,
			countIf(ImplementationComplexity = 'EASY') as easy_fixes,
			countIf(ImplementationComplexity = 'MEDIUM') as medium_fixes,
			countIf(ImplementationComplexity = 'COMPLEX') as complex_fixes,
			countIf(ImplementationComplexity = 'VERY_COMPLEX') as very_complex_fixes
		FROM flamedb.optimization_pattern_summary_v2_local
	`

	ctx := c.Request.Context()
	
	if fetchResponse, err := h.ChClient.FetchOptimizationSummary(ctx, query); err != nil {
		log.Printf("Error fetching optimization summary: %v", err)
		c.Status(http.StatusNoContent)
		return
	} else {
		c.JSON(http.StatusOK, fetchResponse)
	}
}

func (h Handlers) GetOptimizationTechnologies(c *gin.Context) {
	query := `
		SELECT DISTINCT Technology
		FROM flamedb.optimization_pattern_summary_v2_local
		ORDER BY Technology
	`

	ctx := c.Request.Context()
	
	if fetchResponse, err := h.ChClient.FetchOptimizationTechnologies(ctx, query); err != nil {
		log.Printf("Error fetching optimization technologies: %v", err)
		c.Status(http.StatusNoContent)
		return
	} else {
		c.JSON(http.StatusOK, fetchResponse)
	}
}
