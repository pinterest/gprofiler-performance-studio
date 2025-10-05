{
    /*
     * Copyright (C) 2023 Intel Corporation
     *
     * Licensed under the Apache License, Version 2.0 (the "License");
     * you may not use this file except in compliance with the License.
     * You may obtain a copy of the License at
     *
     *    http://www.apache.org/licenses/LICENSE-2.0
     *
     * Unless required by applicable law or agreed to in writing, software
     * distributed under the License is distributed on an "AS IS" BASIS,
     * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
     * See the License for the specific language governing permissions and
     * limitations under the License.
     */
}

import { DATA_URLS } from '../urls';
import useFetchWithRequest from '../useFetchWithRequest';

const useGetOptimizationRecommendations = (filters = {}) => {
    const queryParams = new URLSearchParams();
    
    if (filters.serviceId && filters.serviceId.trim() !== '') queryParams.append('service_id', filters.serviceId.trim());
    if (filters.namespace && filters.namespace.trim() !== '') queryParams.append('namespace', filters.namespace.trim());
    if (filters.technology && filters.technology.trim() !== '') queryParams.append('technology', filters.technology.trim());
    if (filters.complexity && filters.complexity.trim() !== '') queryParams.append('complexity', filters.complexity.trim());
    if (filters.optimizationType && filters.optimizationType.trim() !== '') queryParams.append('optimization_type', filters.optimizationType.trim());
    if (filters.ruleName && filters.ruleName.trim() !== '') queryParams.append('rule_name', filters.ruleName.trim());
    if (filters.minImpact > 0) queryParams.append('min_impact', filters.minImpact.toString());
    if (filters.minPrecision > 0) queryParams.append('min_precision', (filters.minPrecision / 100).toString());
    if (filters.minHosts && filters.minHosts.trim() !== '') queryParams.append('min_hosts', filters.minHosts.trim());
    queryParams.append('limit', '1000'); // Show all recommendations

    const url = `${DATA_URLS.GET_OPTIMIZATION_RECOMMENDATIONS}?${queryParams.toString()}`;

    const { data, loading, error } = useFetchWithRequest(
        { url },
        {
            refreshDeps: [filters.serviceId, filters.namespace, filters.technology, filters.complexity, filters.optimizationType, filters.ruleName, filters.minImpact, filters.minPrecision, filters.minHosts],
            pollingInterval: 60000, // Refresh every minute
            pollingWhenHidden: false,
        }
    );

    return {
        recommendations: data?.result || [],
        loading,
        error,
    };
};

export default useGetOptimizationRecommendations;
