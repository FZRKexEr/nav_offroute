import { spawn } from 'node:child_process';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { defineConfig } from 'vite';

const frontendDir = dirname(fileURLToPath(import.meta.url));
const helperScript = resolve(frontendDir, 'server', 'run_offroute_algorithm.py');

function readRequestBody(req, maxBytes = 25 * 1024 * 1024) {
  return new Promise((resolveBody, rejectBody) => {
    let body = '';
    let size = 0;

    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > maxBytes) {
        rejectBody(new Error('请求体过大，超过 25 MB 限制'));
        req.destroy();
        return;
      }
      body += chunk.toString('utf-8');
    });

    req.on('end', () => resolveBody(body));
    req.on('error', (error) => rejectBody(error));
  });
}

function getPythonCandidates() {
  const candidates = [];

  if (process.env.PYTHON) {
    candidates.push(process.env.PYTHON);
  }

  if (process.env.VIRTUAL_ENV) {
    candidates.push(join(process.env.VIRTUAL_ENV, 'bin', 'python'));
  }

  candidates.push('python3', 'python');
  return [...new Set(candidates)];
}

function runCommand(command, args) {
  return new Promise((resolveRun, rejectRun) => {
    const child = spawn(command, args, {
      cwd: frontendDir,
      env: process.env,
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';

    child.stdout.on('data', (chunk) => {
      stdout += chunk.toString('utf-8');
    });

    child.stderr.on('data', (chunk) => {
      stderr += chunk.toString('utf-8');
    });

    child.on('error', (error) => {
      rejectRun(error);
    });

    child.on('close', (code) => {
      if (code === 0) {
        resolveRun(stdout);
        return;
      }

      rejectRun(
        new Error(stderr.trim() || `命令退出码 ${code}`),
      );
    });
  });
}

async function runPythonHelper(args) {
  let notFoundCount = 0;

  for (const command of getPythonCandidates()) {
    try {
      return await runCommand(command, [helperScript, ...args]);
    } catch (error) {
      if (error && typeof error === 'object' && error.code === 'ENOENT') {
        notFoundCount += 1;
        continue;
      }
      throw error;
    }
  }

  if (notFoundCount) {
    throw new Error('未找到可用的 Python 解释器，请先激活虚拟环境或设置 PYTHON 环境变量');
  }

  throw new Error('无法启动 Python 解释器');
}

function sendJson(res, statusCode, payload) {
  res.statusCode = statusCode;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.end(JSON.stringify(payload));
}

async function handleRunDetector(req, res) {
  if (req.method !== 'POST') {
    sendJson(res, 405, { error: '只支持 POST 请求' });
    return;
  }

  let tempDir = null;

  try {
    const rawBody = await readRequestBody(req);
    const body = JSON.parse(rawBody || '{}');
    const algorithmName = String(body.algorithmName || '').trim();
    const algorithmSource = String(body.algorithmSource || '');
    const routeGeojson = body.routeGeojson;
    const gpsGeojson = body.gpsGeojson;

    if (!algorithmName || !algorithmSource) {
      sendJson(res, 400, { error: '请先手动选择 Python 算法文件' });
      return;
    }

    if (!routeGeojson || !gpsGeojson) {
      sendJson(res, 400, { error: '缺少 route/gps GeoJSON 数据' });
      return;
    }

    tempDir = await mkdtemp(join(tmpdir(), 'offroute-viewer-'));

    const algorithmPath = join(tempDir, algorithmName.endsWith('.py') ? algorithmName : `${algorithmName}.py`);
    const routePath = join(tempDir, 'route.geojson');
    const gpsPath = join(tempDir, 'gps.geojson');

    await writeFile(algorithmPath, algorithmSource, 'utf-8');
    await writeFile(routePath, JSON.stringify(routeGeojson), 'utf-8');
    await writeFile(gpsPath, JSON.stringify(gpsGeojson), 'utf-8');

    const stdout = await runPythonHelper([
      '--algorithm',
      algorithmPath,
      '--route',
      routePath,
      '--gps',
      gpsPath,
    ]);

    sendJson(res, 200, JSON.parse(stdout));
  } catch (error) {
    sendJson(res, 500, {
      error: error instanceof Error ? error.message : '算法执行失败',
    });
  } finally {
    if (tempDir) {
      await rm(tempDir, { recursive: true, force: true });
    }
  }
}

function offrouteApiPlugin() {
  const middleware = async (req, res, next) => {
    const pathname = new URL(req.url || '/', 'http://127.0.0.1').pathname;

    if (pathname !== '/api/run-detector') {
      next();
      return;
    }

    await handleRunDetector(req, res);
  };

  return {
    name: 'offroute-local-api',
    configureServer(server) {
      server.middlewares.use(middleware);
    },
    configurePreviewServer(server) {
      server.middlewares.use(middleware);
    },
  };
}

export default defineConfig({
  plugins: [offrouteApiPlugin()],
});
