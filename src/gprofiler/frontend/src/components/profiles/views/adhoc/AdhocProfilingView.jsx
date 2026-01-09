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

import {
    Box,
    CircularProgress,
    FormControl,
    InputLabel,
    MenuItem,
    Select,
    Table,
    TableBody,
    TableCell,
    TableContainer,
    TableHead,
    TableRow,
    Typography,
    Paper,
    Alert,
} from '@mui/material';
import { useContext, useEffect, useState } from 'react';
import { stringify } from 'query-string';

import { DATA_URLS } from '@/api/urls';
import useFetchWithRequest from '@/api/hooks/useFetchWithRequest';
import Flexbox from '@/components/common/layout/Flexbox';
import { SelectorsContext } from '@/states';
import { FilterTagsContext } from '@/states/filters/FiltersTagsContext';
import { getStartEndDateTimeFromSelection } from '@/utils/dateTimeUtils';

const AdhocProfilingView = () => {
    const { selectedService, timeSelection } = useContext(SelectorsContext);
    const { activeFilterTag } = useContext(FilterTagsContext);
    const [selectedFlamegraph, setSelectedFlamegraph] = useState('');
    const [flamegraphContent, setFlamegraphContent] = useState('');
    const [loadingContent, setLoadingContent] = useState(false);

    // Get time parameters
    const timeParams = getStartEndDateTimeFromSelection(timeSelection);
    
    // Build request parameters
    const requestParams = {
        serviceName: selectedService,
        ...timeParams,
        filter: activeFilterTag?.filter ? JSON.stringify(activeFilterTag) : undefined,
    };

    // Fetch available flamegraph files
    const { data: flamegraphFiles, loading: loadingFiles, error } = useFetchWithRequest(
        {
            url: `${DATA_URLS.GET_ADHOC_FLAMEGRAPHS}?${stringify(requestParams)}`,
        },
        {
            refreshDeps: [selectedService, timeSelection, activeFilterTag],
            ready: !!selectedService,
        }
    );

    // Fetch selected flamegraph content
    const fetchFlamegraphContent = async (filename) => {
        if (!filename) return;
        
        setLoadingContent(true);
        try {
            const contentParams = {
                serviceName: selectedService,
                filename: filename,
                filter: activeFilterTag?.filter ? JSON.stringify(activeFilterTag) : undefined,
            };
            
            const response = await fetch(`${DATA_URLS.GET_ADHOC_FLAMEGRAPH_CONTENT}?${stringify(contentParams)}`);
            if (response.ok) {
                const result = await response.json();
                setFlamegraphContent(result.content);
            } else {
                console.error('Failed to fetch flamegraph content');
                setFlamegraphContent('');
            }
        } catch (error) {
            console.error('Error fetching flamegraph content:', error);
            setFlamegraphContent('');
        } finally {
            setLoadingContent(false);
        }
    };

    // Handle flamegraph selection
    const handleFlamegraphSelect = (filename) => {
        setSelectedFlamegraph(filename);
        fetchFlamegraphContent(filename);
    };

    // Format timestamp for display
    const formatTimestamp = (filename) => {
        const timestampMatch = filename.match(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})/);
        if (timestampMatch) {
            return new Date(timestampMatch[1]).toLocaleString();
        }
        return filename;
    };

    if (error) {
        return (
            <Box p={3}>
                <Alert severity="error">
                    Failed to load adhoc flamegraph files. Please try again later.
                </Alert>
            </Box>
        );
    }

    return (
        <Flexbox column spacing={3} sx={{ height: '100%', p: 3 }}>
            {/* Header */}
            <Typography variant="h6" gutterBottom>
                Adhoc Profiling - Select Flamegraph
            </Typography>

            {/* File Selection */}
            <Box sx={{ minHeight: 300 }}>
                {loadingFiles ? (
                    <Box display="flex" justifyContent="center" alignItems="center" height={200}>
                        <CircularProgress />
                        <Typography variant="body2" sx={{ ml: 2 }}>
                            Loading flamegraph files...
                        </Typography>
                    </Box>
                ) : flamegraphFiles && flamegraphFiles.length > 0 ? (
                    <>
                        {/* Dropdown Selector */}
                        <FormControl fullWidth sx={{ mb: 3 }}>
                            <InputLabel>Select Flamegraph File</InputLabel>
                            <Select
                                value={selectedFlamegraph}
                                label="Select Flamegraph File"
                                onChange={(e) => handleFlamegraphSelect(e.target.value)}
                            >
                                {flamegraphFiles.map((file) => (
                                    <MenuItem key={file.filename} value={file.filename}>
                                        {formatTimestamp(file.filename)} - {file.hostname || 'Unknown Host'}
                                    </MenuItem>
                                ))}
                            </Select>
                        </FormControl>

                        {/* Table View */}
                        <TableContainer component={Paper} sx={{ maxHeight: 400 }}>
                            <Table stickyHeader>
                                <TableHead>
                                    <TableRow>
                                        <TableCell>Timestamp</TableCell>
                                        <TableCell>Hostname</TableCell>
                                        <TableCell>File Size</TableCell>
                                        <TableCell>Action</TableCell>
                                    </TableRow>
                                </TableHead>
                                <TableBody>
                                    {flamegraphFiles.map((file) => (
                                        <TableRow 
                                            key={file.filename}
                                            hover
                                            selected={selectedFlamegraph === file.filename}
                                            sx={{ cursor: 'pointer' }}
                                            onClick={() => handleFlamegraphSelect(file.filename)}
                                        >
                                            <TableCell>{formatTimestamp(file.filename)}</TableCell>
                                            <TableCell>{file.hostname || 'Unknown'}</TableCell>
                                            <TableCell>{file.size ? `${Math.round(file.size / 1024)} KB` : 'N/A'}</TableCell>
                                            <TableCell>
                                                <Typography 
                                                    variant="body2" 
                                                    color="primary" 
                                                    sx={{ cursor: 'pointer' }}
                                                >
                                                    {selectedFlamegraph === file.filename ? 'Selected' : 'Select'}
                                                </Typography>
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </TableContainer>
                    </>
                ) : (
                    <Alert severity="info">
                        No adhoc flamegraph files found for the selected time range and filters.
                    </Alert>
                )}
            </Box>

            {/* Flamegraph Viewer */}
            {selectedFlamegraph && (
                <Box sx={{ flex: 1, minHeight: 400 }}>
                    <Typography variant="h6" gutterBottom>
                        Selected Flamegraph: {formatTimestamp(selectedFlamegraph)}
                    </Typography>
                    
                    {loadingContent ? (
                        <Box display="flex" justifyContent="center" alignItems="center" height={300}>
                            <CircularProgress />
                            <Typography variant="body2" sx={{ ml: 2 }}>
                                Loading flamegraph content...
                            </Typography>
                        </Box>
                    ) : flamegraphContent ? (
                        <iframe
                            style={{
                                width: '100%',
                                height: '500px',
                                border: '1px solid #ddd',
                                borderRadius: '4px',
                            }}
                            title={`Flamegraph: ${selectedFlamegraph}`}
                            srcDoc={flamegraphContent}
                        />
                    ) : selectedFlamegraph ? (
                        <Alert severity="warning">
                            Failed to load flamegraph content. Please try selecting another file.
                        </Alert>
                    ) : null}
                </Box>
            )}
        </Flexbox>
    );
};

export default AdhocProfilingView;
