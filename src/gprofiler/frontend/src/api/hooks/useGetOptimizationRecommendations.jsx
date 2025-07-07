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
    
    if (filters.serviceId) queryParams.append('service_id', filters.serviceId);
    if (filters.technology) queryParams.append('technology', filters.technology);
    if (filters.complexity) queryParams.append('complexity', filters.complexity);
    if (filters.minImpact > 0) queryParams.append('min_impact', filters.minImpact.toString());
    queryParams.append('limit', '100');

    const url = `${DATA_URLS.GET_OPTIMIZATION_RECOMMENDATIONS}?${queryParams.toString()}`;

    const { data, loading, error } = useFetchWithRequest(
        { url },
        {
            refreshDeps: [filters.serviceId, filters.technology, filters.complexity, filters.minImpact],
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
