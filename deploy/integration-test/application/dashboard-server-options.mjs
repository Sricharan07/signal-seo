import { openSync, closeSync, fstatSync, readSync, constants } from 'node:fs';

export function readDashboardOrigin(path) {
  const descriptor = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const info = fstatSync(descriptor);
    if (!info.isFile() || info.nlink !== 1 || (info.mode & 0o077) !== 0 || info.size < 1 || info.size > 4096) {
      throw new Error('Protected dashboard configuration required.');
    }
    const buffer = Buffer.alloc(4097);
    let length = 0;
    for (;;) {
      const count = readSync(descriptor, buffer, length, buffer.length - length, null);
      length += count;
      if (length > 4096) throw new Error('Protected dashboard configuration required.');
      if (count === 0) break;
    }
    const source = buffer.subarray(0, length).toString('utf8');
    const data = JSON.parse(source);
    if (data === null || typeof data !== 'object' || Array.isArray(data) ||
        Object.keys(data).join(',') !== 'origin' || typeof data.origin !== 'string' ||
        source !== JSON.stringify(data)) {
      throw new Error('Protected dashboard configuration required.');
    }
    dashboardServerOptions(data.origin, data.origin);
    return data.origin;
  } finally { closeSync(descriptor); }
}

export function dashboardServerOptions(origin, configuredOrigin) {
  let url;
  try { url = new URL(origin); } catch { throw new Error('Exact dedicated dashboard origin required.'); }
  if (typeof configuredOrigin !== 'string' || origin !== configuredOrigin ||
      url.protocol !== 'https:' || url.origin !== origin || url.port || url.username || url.password ||
      /^\d+\.\d+\.\d+\.\d+$/.test(url.hostname) ||
      !/^[a-z0-9]+(?:[.-][a-z0-9]+)+$/.test(url.hostname)) {
    throw new Error('Exact dedicated dashboard origin required.');
  }
  // Next uses these values for absolute request URLs, not our TLS listener.
  return { dev: false, hostname: url.hostname, port: 443 };
}
