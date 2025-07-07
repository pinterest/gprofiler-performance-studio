

-- OPTIMIZATION RULES TABLE SCHEMA
-- This table stores community and private performance optimization rules
-- Similar to IDS/IPS rule systems like Snort, enabling collaborative
-- performance engineering across the industry

CREATE TABLE IF NOT EXISTS flamedb.optimization_rules_local ON CLUSTER '{cluster}' (
    
    -- RULE IDENTIFICATION
    rule_id String COMMENT 'Unique rule identifier (e.g., JAVA_HOTSPOT_GC_001, PYTHON_PANDAS_ITERROWS_001)',
    rule_name String COMMENT 'Human-readable rule name for display purposes',
    
    -- PATTERN MATCHING - Core functionality for rule detection
    callstack_pattern String COMMENT 'Regex pattern to match against CallStackName column in samples tables',
    platform_pattern String COMMENT 'Regex pattern for comprehensive platform/environment matching including: Java/Python/Go versions, JDK versions, x86/ARM64, AWS/GCP/Azure, instance types (m5.large, c5.xlarge), container environments (docker, k8s), OS versions - can be fetched from perfspect',
    
    -- RULE METADATA
    technology_stack LowCardinality(String) COMMENT 'Primary technology: Java, Python, Go, Node.js, C/C++, Database, AWS, etc.',
    rule_category LowCardinality(String) COMMENT 'Performance category: GC, Memory, IO, Network, CPU, Algorithm, Database, etc.',
    
    -- OPTIMIZATION TYPE CLASSIFICATION
    optimization_type Enum8('HARDWARE'=1, 'SOFTWARE'=2, 'UTILIZATION'=3) COMMENT 'Type of optimization: HARDWARE=CPU/memory/storage changes, SOFTWARE=code/config changes, UTILIZATION=resource rightsizing/scheduling',

    -- DESCRIPTIONS
    description String COMMENT 'Human-readable description of the performance inefficiency',
    optimization_description String COMMENT 'Detailed step-by-step optimization recommendation',
    
    -- OPTIMIZATION IMPACT ESTIMATES (As requested in GitHub issue)
    relative_optimization_efficiency_min Float32 COMMENT 'Minimum expected performance improvement percentage (0.0-100.0)',
    relative_optimization_efficiency_max Float32 COMMENT 'Maximum expected performance improvement percentage (0.0-100.0)',
    
    -- ACCURACY METRICS (As requested in GitHub issue)
    precision_score Float32 COMMENT 'Precision: probability of being true positive (1 - False positive / total). Range 0.0-1.0',
    accuracy_score Float32 COMMENT 'Accuracy: measure to get visibility on savings impact (1 - ((proposed win - actual win)/proposed win)) * 100. Range 0.0-1.0',
    
    -- IMPLEMENTATION CHARACTERISTICS  
    implementation_complexity Enum8('EASY'=1, 'MEDIUM'=2, 'COMPLEX'=3, 'VERY_COMPLEX'=4) DEFAULT 'MEDIUM'
        COMMENT 'Implementation difficulty: EASY=config change, MEDIUM=code change, COMPLEX=architecture, VERY_COMPLEX=major rewrite',
    
    -- RULE MANAGEMENT AND GOVERNANCE
    rule_source Enum8('COMMUNITY'=1, 'PRIVATE'=2, 'VERIFIED'=3, 'EXPERIMENTAL'=4) DEFAULT 'COMMUNITY'
        COMMENT 'Rule source: COMMUNITY=open source, PRIVATE=internal, VERIFIED=tested, EXPERIMENTAL=unproven',
    rule_status Enum8('ACTIVE'=1, 'DEPRECATED'=2, 'EXPERIMENTAL'=3, 'DISABLED'=4) DEFAULT 'ACTIVE'
        COMMENT 'Current rule status for lifecycle management',
  
    -- METADATA AND CONTEXT
    tags Array(String) DEFAULT [] COMMENT 'Tags for categorization and filtering (e.g., [memory, gc, hotspot])',
    documentation_links Array(String) DEFAULT [] COMMENT 'Links to documentation, benchmarks, and resources',
   
    -- NEW COLUMNS FOR METRICS AND METADATA
    metrics String DEFAULT '{}' COMMENT 'JSON metrics configuration for thresholds, patterns and monitoring windows',
    metadata String DEFAULT '{}' COMMENT 'JSON metadata for additional rule configuration and context',
   
    -- AUDIT TRAIL
    created_date DateTime DEFAULT now() COMMENT 'When this rule was created',
    updated_date DateTime DEFAULT now() COMMENT 'When this rule was last modified',
    created_by String COMMENT 'Who created this rule (team, individual, or system)',
    
    -- COMPUTED FIELDS FOR OPTIMIZATION
    rule_hash UInt64 MATERIALIZED cityHash64(concat(rule_id, callstack_pattern, platform_pattern)) 
        COMMENT 'Hash of patterns for fast matching and deduplication'
    
) ENGINE = ReplicatedMergeTree(
    -- ZooKeeper path for coordination between replicas
    '/clickhouse/{installation}/{cluster}/tables/{shard}/{database}/{table}', 
    -- Unique replica identifier (e.g., 'replica-1', 'replica-2')
    '{replica}'
)
-- PARTITION BY: Physically separate data into directories for faster queries
-- Creates separate folders for each (rule_source, optimization_type) combination
-- Example: /COMMUNITY_HARDWARE/, /PRIVATE_SOFTWARE/, /VERIFIED_UTILIZATION/
PARTITION BY (rule_source, optimization_type)
-- ORDER BY: Defines the primary key and sort order for data storage
-- ClickHouse sorts data by these columns for efficient range queries
ORDER BY (optimization_type, technology_stack, rule_id)
-- INDEX GRANULARITY: Number of rows per index entry (8192 = ClickHouse default)
-- Smaller values = more index entries = faster queries but more memory usage
-- 8192 provides good balance for most workloads
SETTINGS index_granularity = 8192
COMMENT 'Performance optimization rules for automated detection and recommendation system';

-- Create distributed table for cluster-wide access
-- This table automatically routes queries to all shards in the cluster
CREATE TABLE IF NOT EXISTS flamedb.optimization_rules ON CLUSTER '{cluster}' AS flamedb.optimization_rules_local
ENGINE = Distributed(
    '{cluster}',                     -- Cluster name
    'flamedb',                       -- Database name  
    'optimization_rules_local',      -- Local table name
    rule_hash                        -- Sharding key (distributes data across nodes)
)
COMMENT 'Distributed access to optimization rules across the cluster';

-- NOTE: Advanced indexes can be added later for performance optimization
-- Examples of indexes that could be useful:
-- - tokenbf_v1 for full-text search on callstack_pattern and platform_pattern  
-- - minmax for filtering on optimization_type and rule_priority
-- - bloom_filter for exact matches on rule_id

-- SAMPLE DATA INSERTION - Demonstrating the three optimization types

INSERT INTO flamedb.optimization_rules_local VALUES
(
    -- UTILIZATION Optimization: Java HotSpot Full GC - requires JVM tuning
    'JAVA_HOTSPOT_FULLGC_001',
    'Java HotSpot Full GC Performance Issue',
    '.*(FullGC|G1.*Collection|ConcurrentMarkSweep).*',
    '.*(hotspot|OpenJDK|Oracle.*JDK).*(x86_64|amd64).*(m5\.large|m5\.xlarge|c5\.large|c5\.xlarge|r5\.large|r5\.xlarge).*',
    'Java',
    'GC',
    'UTILIZATION',
    'Frequent Full GC events causing application pauses on x86_64 compute-optimized instances',
    'UTILIZATION optimization: Increase heap size to match instance memory (m5.large=8GB heap, m5.xlarge=16GB heap). Switch to G1GC: -XX:+UseG1GC -XX:MaxGCPauseMillis=200. For c5 instances, use parallel GC settings.',
    0.0, 0.0,
    0.5, 0.5,
    'MEDIUM',
    'VERIFIED', 'ACTIVE',
    ['java', 'gc', 'memory', 'hotspot', 'hardware', 'aws'],
    ['https://docs.oracle.com/javase/8/docs/technotes/guides/vm/gctuning/'],
    NULL, NULL,
    now() - INTERVAL 60 DAY, now(), 'java-performance-team'
),
(
    -- HARDWARE Optimization: Go memory allocation - resource rightsizing
    'GO_RUNTIME_MALLOC_001',
    'Go Runtime Memory Over-allocation',
    '.*(runtime\.mallocgc|runtime\.newobject).*',
    '.*(go1\.[0-9]+|golang).*(t3\.micro|t3\.small|t2\.micro|t2\.small).*',
    'Go',
    'Memory',
    'HARDWARE',
    'Go services over-allocated on small AWS instances with excessive memory allocation',
    'HARDWARE optimization: Right-size instance from t3.micro (1GB) to t3.small (2GB) or use memory pools. Set GOGC=50 for memory-constrained instances. Implement sync.Pool for object reuse.',
    0.0, 0.0,
    0.5, 0.5,
    'EASY',
    'COMMUNITY', 'ACTIVE',
    ['go', 'memory', 'utilization', 'rightsizing', 'aws'],
    ['https://go.dev/doc/gc-guide'],
    NULL, NULL,
    now() - INTERVAL 45 DAY, now() - INTERVAL 2 DAY, 'go-performance-team'
),
(
    -- SOFTWARE Optimization: Java Regex Pattern Compilation
    'JAVA_REGEX_COMPILE_001',
    'Java Regex Pattern Repeated Compilation',
    '.*(Pattern\.compile|Matcher\.<init>|java\.util\.regex\.Pattern\.compile).*',
    '.*(hotspot|OpenJDK|Oracle.*JDK).*',
    'Java',
    'CPU',
    'SOFTWARE',
    'Repeated regex pattern compilation in hot code paths causing CPU overhead',
    'SOFTWARE optimization: Pre-compile Pattern objects as static final fields. Replace Pattern.compile(regex).matcher(input) with static Pattern PATTERN = Pattern.compile(regex); then PATTERN.matcher(input). Use Pattern.quote() for literal strings.',
    0.0, 0.0,
    0.5, 0.5,
    'EASY',
    'COMMUNITY', 'ACTIVE',
    ['java', 'regex', 'pattern', 'compilation', 'cpu'],
    ['https://docs.oracle.com/javase/8/docs/api/java/util/regex/Pattern.html'],
    NULL, NULL,
    now() - INTERVAL 30 DAY, now() - INTERVAL 1 DAY, 'java-performance-team'
),
(
    -- SOFTWARE Optimization: Java String Concatenation with StringBuilder
    'JAVA_STRING_CONCAT_001',
    'Java String Concatenation Performance',
    '.*(StringBuilder\.<init>|String\.concat|String\.\+).*',
    '.*(hotspot|OpenJDK|Oracle.*JDK).*',
    'Java',
    'Memory',
    'SOFTWARE',
    'Inefficient string concatenation using + operator in loops causing excessive object creation',
    'SOFTWARE optimization: Replace string concatenation in loops with StringBuilder. Use StringBuilder sb = new StringBuilder(estimatedSize); sb.append(str1).append(str2); return sb.toString(). For known capacity, pre-size StringBuilder.',
    0.0, 0.0,
    0.5, 0.5,
    'EASY',
    'COMMUNITY', 'ACTIVE',
    ['java', 'string', 'concatenation', 'stringbuilder', 'memory'],
    ['https://docs.oracle.com/javase/8/docs/api/java/lang/StringBuilder.html'],
    NULL, NULL,
    now() - INTERVAL 25 DAY, now(), 'java-performance-team'
),
(
    -- SOFTWARE Optimization: Java HashMap/HashSet Optimization
    'JAVA_HASHMAP_SIZE_001',
    'Java HashMap/HashSet Initial Capacity',
    '.*(HashMap\.<init>|HashSet\.<init>|java\.util\.HashMap\.resize).*',
    '.*(hotspot|OpenJDK|Oracle.*JDK).*',
    'Java',
    'Memory',
    'SOFTWARE',
    'HashMap/HashSet resizing operations due to insufficient initial capacity causing performance degradation',
    'SOFTWARE optimization: Initialize HashMap/HashSet with appropriate capacity: new HashMap<>(expectedSize * 4/3 + 1) to avoid resize operations. Use LinkedHashMap for insertion-order preservation. Consider THashMap for primitive keys.',
    0.0, 0.0,
    0.5, 0.5,
    'EASY',
    'COMMUNITY', 'ACTIVE',
    ['java', 'hashmap', 'hashset', 'capacity', 'resize', 'memory'],
    ['https://docs.oracle.com/javase/8/docs/api/java/util/HashMap.html'],
    NULL, NULL,
    now() - INTERVAL 20 DAY, now(), 'java-performance-team'
),
(
    -- SOFTWARE Optimization: Java Serialization/Deserialization
    'JAVA_SERIALIZATION_001',
    'Java Object Serialization Performance',
    '.*(ObjectOutputStream\.writeObject|ObjectInputStream\.readObject|Serializable).*',
    '.*(hotspot|OpenJDK|Oracle.*JDK).*',
    'Java',
    'IO',
    'SOFTWARE',
    'Slow Java native serialization causing performance bottlenecks in distributed systems',
    'SOFTWARE optimization: Replace Java serialization with faster alternatives: Kryo, Protocol Buffers, Avro, or Jackson for JSON. Implement custom writeExternal/readExternal methods. Use transient keyword for non-essential fields.',
    0.0, 0.0,
    0.5, 0.5,
    'MEDIUM',
    'VERIFIED', 'ACTIVE',
    ['java', 'serialization', 'kryo', 'protobuf', 'jackson', 'performance'],
    ['https://github.com/EsotericSoftware/kryo', 'https://developers.google.com/protocol-buffers/docs/javatutorial'],
    NULL, NULL,
    now() - INTERVAL 40 DAY, now(), 'java-performance-team'
),
(
    -- SOFTWARE Optimization: Java String Concatenation in General
    'JAVA_STRING_CONCATENATION_001',
    'Java String Concatenation Optimization',
    '.*(String\.valueOf|String\.concat|StringBuilder\.toString).*',
    '.*(hotspot|OpenJDK|Oracle.*JDK).*',
    'Java',
    'CPU',
    'SOFTWARE',
    'Inefficient string concatenation patterns causing excessive CPU and memory usage',
    'SOFTWARE optimization: Use String.join() for delimiter-separated strings, StringBuilder for loops, String.format() for complex formatting. Replace str1 + str2 + str3 with String.join("", str1, str2, str3) or StringBuilder.',
    0.0, 0.0,
    0.5, 0.5,
    'EASY',
    'COMMUNITY', 'ACTIVE',
    ['java', 'string', 'concatenation', 'join', 'format'],
    ['https://docs.oracle.com/javase/8/docs/api/java/lang/String.html#join-java.lang.CharSequence-java.lang.CharSequence...-'],
    NULL, NULL,
    now() - INTERVAL 15 DAY, now(), 'java-performance-team'
),
(
    -- SOFTWARE Optimization: General Compression
    'GENERAL_COMPRESSION_001',
    'Data Compression Algorithm Optimization',
    '.*(gzip|deflate|lz4|snappy|zstd|brotli).*',
    '.*',
    'General',
    'IO',
    'SOFTWARE',
    'Suboptimal compression algorithm selection causing unnecessary CPU overhead or poor compression ratios',
    'SOFTWARE optimization: Choose compression based on use case: LZ4/Snappy for speed, GZIP for balanced compression, Zstandard for best ratio. Use streaming compression for large datasets. Set appropriate compression levels (1-9).',
    0.0, 0.0,
    0.5, 0.5,
    'MEDIUM',
    'COMMUNITY', 'ACTIVE',
    ['compression', 'gzip', 'lz4', 'snappy', 'zstd', 'performance'],
    ['https://facebook.github.io/zstd/', 'https://lz4.github.io/lz4/'],
    NULL, NULL,
    now() - INTERVAL 35 DAY, now(), 'performance-team'
),
(
    -- SOFTWARE Optimization: JSON Processing
    'JSON_PROCESSING_001',
    'JSON Parsing and Generation Optimization',
    '.*(JSON\.parse|JSON\.stringify|JsonParser|ObjectMapper).*',
    '.*',
    'General',
    'CPU',
    'SOFTWARE',
    'Inefficient JSON processing libraries or patterns causing CPU bottlenecks',
    'SOFTWARE optimization: Use streaming JSON parsers for large files, faster libraries (Jackson > Gson > org.json), object reuse with ObjectMapper, disable unnecessary features like pretty printing in production.',
    0.0, 0.0,
    0.5, 0.5,
    'MEDIUM',
    'COMMUNITY', 'ACTIVE',
    ['json', 'jackson', 'gson', 'parsing', 'streaming'],
    ['https://github.com/FasterXML/jackson', 'https://github.com/google/gson'],
    NULL, NULL,
    now() - INTERVAL 28 DAY, now(), 'performance-team'
),
(
    -- SOFTWARE Optimization: String Trimming
    'STRING_TRIM_OPTIMIZATION_001',
    'String Trimming and Whitespace Handling',
    '.*(String\.trim|String\.strip|String\.replaceAll.*\\s).*',
    '.*',
    'General',
    'CPU',
    'SOFTWARE',
    'Inefficient string trimming operations in high-frequency code paths',
    'SOFTWARE optimization: Use String.strip() over trim() in Java 11+, implement custom trim for specific whitespace, cache trimmed results, use StringBuilder for complex whitespace operations, consider regex-free approaches.',
    0.0, 0.0,
    0.5, 0.5,
    'EASY',
    'COMMUNITY', 'ACTIVE',
    ['string', 'trim', 'strip', 'whitespace', 'optimization'],
    ['https://docs.oracle.com/en/java/javase/11/docs/api/java.base/java/lang/String.html#strip()'],
    NULL, NULL,
    now() - INTERVAL 10 DAY, now(), 'performance-team'
),
(
    -- SOFTWARE Optimization: Python List Comprehension
    'PYTHON_LIST_COMPREHENSION_001',
    'Python List Comprehension vs Loop+Append',
    '.*(list.*append.*for|\\[.*for.*in.*\\]).*',
    '.*(python3|Python.*3|cpython).*',
    'Python',
    'CPU',
    'SOFTWARE',
    'Using explicit loops with list.append() instead of list comprehensions causing performance overhead',
    'SOFTWARE optimization: Replace for loops with append() using list comprehensions. Change [result.append(func(x)) for x in items] to [func(x) for x in items]. Use generator expressions for memory efficiency: (func(x) for x in items).',
    0.0, 0.0,
    0.5, 0.5,
    'EASY',
    'COMMUNITY', 'ACTIVE',
    ['python', 'list', 'comprehension', 'loop', 'performance'],
    ['https://docs.python.org/3/tutorial/datastructures.html#list-comprehensions'],
    NULL, NULL,
    now() - INTERVAL 15 DAY, now(), 'python-performance-team'
),
(
    -- SOFTWARE Optimization: Python Global Interpreter Lock (GIL) Impact
    'PYTHON_GIL_THREADING_001',
    'Python Threading vs Multiprocessing for CPU Tasks',
    '.*(threading\.Thread|concurrent\.futures\.ThreadPool).*',
    '.*(python3|Python.*3|cpython).*',
    'Python',
    'CPU',
    'SOFTWARE',
    'Using threading for CPU-bound tasks in Python which are limited by GIL causing poor scalability',
    'SOFTWARE optimization: Replace threading.Thread with multiprocessing.Process for CPU-bound tasks. Use concurrent.futures.ProcessPoolExecutor instead of ThreadPoolExecutor. For I/O-bound tasks, keep using threading or asyncio.',
    0.0, 0.0,
    0.5, 0.5,
    'MEDIUM',
    'COMMUNITY', 'ACTIVE',
    ['python', 'gil', 'threading', 'multiprocessing', 'cpu'],
    ['https://docs.python.org/3/library/multiprocessing.html'],
    NULL, NULL,
    now() - INTERVAL 20 DAY, now(), 'python-performance-team'
),
(
    -- SOFTWARE Optimization: Go Slice Preallocation
    'GO_SLICE_PREALLOCATION_001',
    'Go Slice Memory Reallocation Performance',
    '.*(append.*slice|make.*slice.*0).*',
    '.*(go1\.[0-9]+|golang).*',
    'Go',
    'Memory',
    'SOFTWARE',
    'Go slices growing through append() without preallocation causing multiple memory reallocations',
    'SOFTWARE optimization: Preallocate slice capacity when final size is known: make([]Type, 0, capacity) instead of make([]Type, 0). Use slice := make([]Type, 0, expectedSize) before append loop. Avoid growing slices in tight loops.',
    0.0, 0.0,
    0.5, 0.5,
    'EASY',
    'COMMUNITY', 'ACTIVE',
    ['go', 'slice', 'preallocation', 'append', 'memory'],
    ['https://go.dev/blog/slices-intro'],
    NULL, NULL,
    now() - INTERVAL 12 DAY, now(), 'go-performance-team'
),
(
    -- SOFTWARE Optimization: Go String Formatting Performance
    'GO_STRING_FORMATTING_001',
    'Go fmt.Sprintf Performance Optimization',
    '.*(fmt\.Sprintf|fmt\.Printf.*string).*',
    '.*(go1\.[0-9]+|golang).*',
    'Go',
    'CPU',
    'SOFTWARE',
    'Heavy use of fmt.Sprintf for string formatting in hot code paths causing CPU overhead',
    'SOFTWARE optimization: Use strings.Builder for complex concatenation, strconv package for primitive conversions. Replace fmt.Sprintf("%s%s", a, b) with strings.Builder or simple concatenation a + b. Use strconv.Itoa() instead of fmt.Sprintf("%d", num).',
    0.0, 0.0,
    0.5, 0.5,
    'EASY',
    'COMMUNITY', 'ACTIVE',
    ['go', 'fmt', 'sprintf', 'string', 'formatting'],
    ['https://pkg.go.dev/strings#Builder'],
    NULL, NULL,
    now() - INTERVAL 18 DAY, now(), 'go-performance-team'
),
(
    -- SOFTWARE Optimization: C++ Vector Reallocation
    'CPP_VECTOR_RESERVE_001',
    'C++ Vector Memory Reallocation Optimization',
    '.*(std::vector.*push_back|vector.*resize).*',
    '.*(gcc|clang|msvc).*',
    'C++',
    'Memory',
    'SOFTWARE',
    'C++ std::vector growing without reserve() causing multiple memory reallocations and copies',
    'SOFTWARE optimization: Call vector.reserve(expectedSize) before push_back loop. Use vector.resize(size) when final size is known. Replace push_back in loops with direct indexing after resize. Consider using std::array for fixed-size arrays.',
    0.0, 0.0,
    0.5, 0.5,
    'EASY',
    'COMMUNITY', 'ACTIVE',
    ['cpp', 'vector', 'reserve', 'push_back', 'memory'],
    ['https://en.cppreference.com/w/cpp/container/vector/reserve'],
    NULL, NULL,
    now() - INTERVAL 22 DAY, now(), 'cpp-performance-team'
),
(
    -- SOFTWARE Optimization: C++ String Concatenation
    'CPP_STRING_CONCAT_001',
    'C++ String Concatenation Performance',
    '.*(std::string.*\\+|string.*append.*loop).*',
    '.*(gcc|clang|msvc).*',
    'C++',
    'Memory',
    'SOFTWARE',
    'C++ string concatenation using + operator in loops causing multiple allocations',
    'SOFTWARE optimization: Use std::stringstream or std::string::reserve() for multiple concatenations. Replace str1 + str2 + str3 with stringstream or single reserve() + append() calls. Consider std::string_view for read-only operations.',
    0.0, 0.0,
    0.5, 0.5,
    'EASY',
    'COMMUNITY', 'ACTIVE',
    ['cpp', 'string', 'concatenation', 'stringstream', 'performance'],
    ['https://en.cppreference.com/w/cpp/io/basic_stringstream'],
    NULL, NULL,
    now() - INTERVAL 14 DAY, now(), 'cpp-performance-team'
);