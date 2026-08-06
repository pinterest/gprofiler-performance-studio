# Spark-on-Kubernetes profiling demos

Two demos that run Spark as real k8s pods so the gProfiler agent DaemonSet
profiles the executor JVMs per thread:

| demo | what runs | use it to see |
|------|-----------|---------------|
| **single** (`spark-demo`) | one CPU-burn app: 6 executors x 4 task threads | per-thread Java flamegraph of one busy Spark app |
| **multi** (`spark-multi-demo`) | 4 *different* apps side by side | **relative weight** of different workloads |

## Why "multi"? (relative weight)

When every executor runs the same kernel, all the per-thread flamegraphs look
alike. `spark-multi` submits four apps that each hammer a different hot path, so
the profile shows visibly different stacks and lets you compare where CPU goes:

| mode | app name | dominant frames |
|------|----------|-----------------|
| `agg`   | `spark-agg`   | `WholeStageCodegen` + transcendental math (`sin/cos/sqrt/log`) |
| `join`  | `spark-join`  | sort-merge join: `UnsafeExternalSorter`, `ShuffleWriter`, `Sorter` |
| `regex` | `spark-regex` | `java.util.regex` (`Pattern`/`Matcher`) + codegen |
| `pyudf` | `spark-pyudf` | Python UDF: JVM `PythonRunner` + Python worker CPU (`appid: pyspark`) |

Example from one 120s host profile (8 executor JVMs, 16-core node):

```
spark-pyudf   43.9% of Spark CPU     (Python worker dominated)
spark-regex   31.6%
spark-join    14.0%
spark-agg     10.4%
```

## Running

```bash
cd deploy/k8s-sandbox

make -f Makefile.k8s spark-demo         # single app, one-shot
make -f Makefile.k8s spark-multi-demo   # 4 distinct apps, one-shot

# granular
make -f Makefile.k8s spark-multi SPARK_MODES="agg join regex pyudf" SPARK_MULTI_SECONDS=1200
make -f Makefile.k8s spark-status
make -f Makefile.k8s spark-profile SPARK_PROFILE_SECONDS=120
make -f Makefile.k8s spark-clean
```

## Files

- `spark_cpu_job.py` — single-app CPU burn (used by `spark-demo`).
- `spark_workloads.py` — multi-mode workloads (used by `spark-multi`), dispatched by `<mode>` arg.
- `Dockerfile` — thin layer over `apache/spark:3.5.1` baking both scripts in.
- `00-rbac.yaml` — `spark` namespace + ServiceAccount/Role so the driver can create executor pods.
- `10-submit-job.yaml` — single-app `spark-submit` Job (cluster mode).
- `11-submit-workload.yaml` — templated per-mode Job (`__SUFFIX__/__MODE__/__APPNAME__/__SECONDS__`).

## Gotchas (learned the hard way)

1. **Profile while the apps are running.** The Spark Jobs exit after
   `SPARK_MULTI_SECONDS`. If they finish before the profile window, async-profiler
   has no JVMs to attach to and you get a Python-only capture. `spark-*-demo`
   size the run window to cover profiling; if you drive it manually, submit with a
   long duration and profile promptly (`spark-status` should show executors
   `Running`).
2. **appid does not split Spark apps on k8s 3.5.** All executors report
   `appid: java: org.apache.spark...KubernetesExecutorBackend`, so
   `--java-collect-spark-app-name-as-appid` won't separate them in the UI.
   gProfiler's Spark detector (`_JavaSparkApplicationIdentifier`) matches
   `org.apache.spark.executor` in argv, which the k8s executor backend main class
   no longer contains. Group by the executor **pod** (its name is a frame in every
   sample) or the `workload=<mode>` pod label instead.
