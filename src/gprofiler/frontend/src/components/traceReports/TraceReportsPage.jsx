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

import { Alert, Box, FormControl, InputLabel, Link, MenuItem, Select, Skeleton, Typography } from '@mui/material';
import _ from 'lodash';
import { useMemo } from 'react';
import { StringParam, useQueryParams, withDefault } from 'use-query-params';

import { DATA_URLS } from '../../api/urls';
import useFetchWithRequest from '../../api/useFetchWithRequest';
import Flexbox from '../common/layout/Flexbox';
import PageHeader from '../common/layout/PageHeader';

const INDEX_REFRESH_INTERVAL = 5 * 60 * 1000;

export const TIME_WINDOWS = [
    { key: '6h', label: 'Last 6 hours', hours: 6 },
    { key: '24h', label: 'Last 24 hours', hours: 24 },
    { key: '3d', label: 'Last 3 days', hours: 72 },
    { key: '7d', label: 'Last 7 days', hours: 168 },
    { key: 'all', label: 'All retained', hours: null },
];

export const runsInWindow = (reports, reportId, windowKey, now = Date.now()) => {
    const hours = _.find(TIME_WINDOWS, { key: windowKey })?.hours;
    return _.filter(
        reports,
        (r) => r.id === reportId && (!hours || now - Date.parse(r.generated_at) <= hours * 3600 * 1000)
    );
};

const formatRun = (run) => {
    const at = new Date(run.generated_at).toLocaleString();
    const wall = run.wall_ms ? ` · ${(run.wall_ms / 1000).toFixed(1)} s` : '';
    return `${at}${wall}${run.trace_id ? ` · ${run.trace_id}` : ''}`;
};

const TraceReportsPage = () => {
    const [query, setQuery] = useQueryParams({
        report: StringParam,
        window: withDefault(StringParam, '24h'),
        run: StringParam,
    });
    const { data, loading, error } = useFetchWithRequest(
        { url: DATA_URLS.GET_TRACE_REPORTS },
        { pollingInterval: INDEX_REFRESH_INTERVAL, pollingWhenHidden: false }
    );

    const reports = useMemo(() => data?.reports || [], [data]);
    const reportOptions = useMemo(
        () => _.uniqBy(reports, 'id').map((r) => ({ id: r.id, label: r.label || r.id, root: r.root })),
        [reports]
    );
    const reportId = query.report || reportOptions[0]?.id;
    const runs = useMemo(() => runsInWindow(reports, reportId, query.window), [reports, reportId, query.window]);
    const run = _.find(runs, { path: query.run }) || runs[0];
    const failure = _.find(data?.errors, { id: reportId });
    const htmlUrl = run ? `${DATA_URLS.GET_TRACE_REPORT_HTML}?path=${encodeURIComponent(run.path)}` : null;

    return (
        <Flexbox column sx={{ height: '100%' }}>
            <PageHeader title='Trace Profiles' />
            <Flexbox alignItems='center' spacing={4} sx={{ px: 6, py: 3, flexWrap: 'wrap' }}>
                <FormControl size='small' sx={{ minWidth: 320 }}>
                    <InputLabel id='trace-report-path'>Trace path</InputLabel>
                    <Select
                        labelId='trace-report-path'
                        label='Trace path'
                        value={reportId || ''}
                        onChange={(e) => setQuery({ report: e.target.value, run: undefined })}>
                        {reportOptions.map((r) => (
                            <MenuItem key={r.id} value={r.id} title={r.root}>
                                {r.label}
                            </MenuItem>
                        ))}
                    </Select>
                </FormControl>
                <FormControl size='small' sx={{ minWidth: 160 }}>
                    <InputLabel id='trace-report-window'>Time window</InputLabel>
                    <Select
                        labelId='trace-report-window'
                        label='Time window'
                        value={query.window}
                        onChange={(e) => setQuery({ window: e.target.value, run: undefined })}>
                        {TIME_WINDOWS.map((w) => (
                            <MenuItem key={w.key} value={w.key}>
                                {w.label}
                            </MenuItem>
                        ))}
                    </Select>
                </FormControl>
                <FormControl size='small' sx={{ minWidth: 340 }} disabled={!runs.length}>
                    <InputLabel id='trace-report-run'>Run ({runs.length})</InputLabel>
                    <Select
                        labelId='trace-report-run'
                        label={`Run (${runs.length})`}
                        value={run?.path || ''}
                        onChange={(e) => setQuery({ run: e.target.value })}>
                        {runs.map((r) => (
                            <MenuItem key={r.path} value={r.path}>
                                {formatRun(r)}
                            </MenuItem>
                        ))}
                    </Select>
                </FormControl>
                {htmlUrl && (
                    <Link href={htmlUrl} target='_blank' rel='noopener noreferrer'>
                        Open in new tab
                    </Link>
                )}
            </Flexbox>
            {failure && (
                <Alert severity='warning' sx={{ mx: 6, mb: 2 }}>
                    Latest run of this report failed: {failure.error}
                </Alert>
            )}
            <Box sx={{ flex: 1, minHeight: 600, mx: 6, mb: 4 }}>
                {loading && !data ? (
                    <Skeleton variant='rectangular' height='100%' animation='wave' />
                ) : error ? (
                    <Alert severity='error'>Could not load trace reports ({error.message}).</Alert>
                ) : htmlUrl ? (
                    <iframe
                        key={htmlUrl}
                        title='trace profile report'
                        src={htmlUrl}
                        sandbox='allow-scripts allow-popups allow-popups-to-escape-sandbox'
                        referrerPolicy='no-referrer'
                        style={{ width: '100%', height: '100%', minHeight: 600, border: 0, background: 'white' }}
                    />
                ) : (
                    <Typography sx={{ mt: 4 }} color='text.secondary'>
                        {reportOptions.length
                            ? 'No runs of this report in the selected time window.'
                            : 'No trace reports have been published yet.'}
                    </Typography>
                )}
            </Box>
        </Flexbox>
    );
};

export default TraceReportsPage;
