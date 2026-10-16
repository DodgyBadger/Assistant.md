// Audit all dependencies; exceptions apply only to named advisories in dev-only nodes.
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

function checkAudit(report, lock, exceptions, today = new Date().toISOString().slice(0, 10)) {
    if (report.error || report.auditReportVersion !== 2 || !report.vulnerabilities
        || typeof report.vulnerabilities !== 'object' || Array.isArray(report.vulnerabilities)) {
        throw new Error('npm returned an unsupported or failed audit report.');
    }
    if (!lock.packages || !Array.isArray(exceptions)) throw new Error('Invalid lockfile or exception list.');
    const allowed = new Map();
    for (const entry of exceptions) {
        if (!/^https:\/\/github\.com\/advisories\/GHSA-[a-z0-9-]+$/.test(entry.advisory)
            || !entry.package || !entry.version || !entry.reason?.trim()
            || !/^\d{4}-\d{2}-\d{2}$/.test(entry.review_by)
            || !Number.isFinite(Date.parse(entry.review_by))
            || new Date(entry.review_by).toISOString().slice(0, 10) !== entry.review_by
            || allowed.has(entry.advisory)) throw new Error('Invalid or duplicate audit exception.');
        if (today >= entry.review_by) throw new Error(`Audit exception expired: ${entry.advisory}; review by ${entry.review_by}.`);
        allowed.set(entry.advisory, entry);
    }
    const used = new Set();
    const blocking = new Set();
    function inspect(name, ancestors = new Set()) {
        const finding = report.vulnerabilities[name];
        if (!finding || finding.name !== name || ancestors.has(name)
            || !Array.isArray(finding.via) || finding.via.length === 0
            || !Array.isArray(finding.nodes) || finding.nodes.length === 0) {
            throw new Error(`Unresolvable npm audit finding: ${name}.`);
        }
        const nodes = finding.nodes.map(node => lock.packages[node]);
        if (nodes.some(node => !node)) throw new Error(`Audit finding missing from lockfile: ${name}.`);
        if (finding.severity === 'critical') blocking.add(`${name}: critical findings cannot be excepted`);
        const devOnly = nodes.every(node => node.dev === true);
        const next = new Set([...ancestors, name]);
        for (const via of finding.via) {
            if (typeof via === 'string') {
                if (!devOnly) blocking.add(`${name}: exception cannot cover a runtime dependency`);
                inspect(via, next);
            } else if (via && typeof via.url === 'string') {
                const exception = allowed.get(via.url);
                if (exception && exception.package === name && via.name === name
                    && via.severity === 'high'
                    && devOnly && nodes.every(node => node.version === exception.version)) {
                    used.add(via.url);
                } else blocking.add(`${name}: ${via.url}`);
            } else throw new Error(`Malformed advisory for ${name}.`);
        }
    }
    for (const [name, finding] of Object.entries(report.vulnerabilities)) {
        if (!['info', 'low', 'moderate', 'high', 'critical'].includes(finding.severity)) {
            throw new Error(`Unknown audit severity for ${name}.`);
        }
        if (['high', 'critical'].includes(finding.severity)) inspect(name);
    }
    for (const entry of exceptions) {
        if (!used.has(entry.advisory)) throw new Error(`Audit exception is no longer applicable; remove or review it: ${entry.advisory}.`);
    }
    return { ignored: [...used].map(url => allowed.get(url)), blocking: [...blocking] };
}

function main() {
    const root = path.resolve(__dirname, '..');
    const audit = spawnSync('npm', ['audit', '--include=dev', '--json'], {
        cwd: root, encoding: 'utf8', maxBuffer: 10 * 1024 * 1024,
    });
    if (audit.error || ![0, 1].includes(audit.status)) throw new Error('npm audit could not complete successfully.', { cause: audit.error });
    const report = JSON.parse(audit.stdout);
    const lock = JSON.parse(fs.readFileSync(path.join(root, 'package-lock.json'), 'utf8'));
    const exceptions = JSON.parse(fs.readFileSync(path.join(__dirname, 'npm-audit-exceptions.json'), 'utf8'));
    const result = checkAudit(report, lock, exceptions);
    for (const entry of result.ignored) {
        console.warn(`::warning::Temporary npm audit exception: ${entry.advisory}; review by ${entry.review_by}. ${entry.reason}`);
    }
    if (result.blocking.length) throw new Error(`Blocking npm vulnerabilities:\n${result.blocking.join('\n')}`);
    console.log('npm audit passed: no unexcepted high or critical findings.');
}

module.exports = { checkAudit };
if (require.main === module) {
    try { main(); }
    catch (error) { console.error(error.message); process.exitCode = 1; }
}
