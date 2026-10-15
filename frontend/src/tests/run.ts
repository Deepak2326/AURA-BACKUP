import fs from 'fs';
import path from 'path';

// Pre-load frontend/.env into process.env for Node test runner
const envPath = path.resolve(process.cwd(), '.env');
if (fs.existsSync(envPath)) {
  const content = fs.readFileSync(envPath, 'utf8');
  content.split('\n').forEach(line => {
    const trimmed = line.trim();
    if (trimmed && !trimmed.startsWith('#')) {
      const idx = trimmed.indexOf('=');
      if (idx !== -1) {
        const k = trimmed.substring(0, idx).trim();
        const v = trimmed.substring(idx + 1).trim();
        process.env[k] = v;
      }
    }
  });
}

const { runFrontendTests } = await import('./api.test');

runFrontendTests().then(({ failed }) => {
  if (failed > 0) {
    process.exit(1);
  }
});
