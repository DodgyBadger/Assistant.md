const assert = require('node:assert/strict');
const { test } = require('node:test');
const { checkAudit } = require('../../scripts/audit-npm.cjs');

const url = 'https://github.com/advisories/GHSA-vfj7-8cjw-p6xm';
function fixture() {
    return {
        report: { auditReportVersion: 2, vulnerabilities: {
            braces: { name: 'braces', severity: 'high', nodes: ['node_modules/braces'],
                via: [{ name: 'braces', severity: 'high', url }] },
            tailwindcss: { name: 'tailwindcss', severity: 'high', nodes: ['node_modules/tailwindcss'], via: ['braces'] },
        } },
        lock: { packages: {
            'node_modules/braces': { version: '3.0.3', dev: true },
            'node_modules/tailwindcss': { version: '3.4.19', dev: true },
        } },
        exceptions: [{ advisory: url, package: 'braces', version: '3.0.3', review_by: '2026-10-30', reason: 'Build-only unpatched dependency.' }],
    };
}
const evaluate = ({ report, lock, exceptions }, today = '2026-10-16') => checkAudit(report, lock, exceptions, today);

test('only the approved originating advisory and its transitive findings are exempted', () => {
    const result = evaluate(fixture());
    assert.equal(result.ignored.length, 1);
    assert.deepEqual(result.blocking, []);
});
test('new high and critical advisories still block, including on the excepted package', () => {
    for (const severity of ['high', 'critical']) {
        const data = fixture();
        data.report.vulnerabilities.braces.via.push({ name: 'braces', severity, url: 'https://github.com/advisories/GHSA-other' });
        assert.equal(evaluate(data).blocking.length, 1);
    }
});
test('a critical severity escalation of the approved advisory still blocks', () => {
    const data = fixture();
    data.report.vulnerabilities.braces.severity = 'critical';
    assert.match(evaluate(data).blocking[0], /critical findings/);
});
test('a runtime dependency anywhere in the chain cannot use the exception', () => {
    for (const name of ['braces', 'tailwindcss']) {
        const data = fixture();
        delete data.lock.packages[`node_modules/${name}`].dev;
        if (name === 'braces') assert.throws(() => evaluate(data), /no longer applicable/);
        else assert.match(evaluate(data).blocking[0], /runtime dependency/);
    }
});
test('review deadline fails on that UTC date and afterward', () => {
    for (const date of ['2026-10-30', '2026-10-31']) assert.throws(() => evaluate(fixture(), date), /expired/);
    assert.deepEqual(evaluate(fixture(), '2026-10-29').blocking, []);
});
test('a removed advisory or changed dependency version requires exception cleanup', () => {
    const clean = fixture();
    clean.report.vulnerabilities = {};
    assert.throws(() => evaluate(clean), /no longer applicable/);
    const changed = fixture();
    changed.lock.packages['node_modules/braces'].version = '3.0.4';
    assert.throws(() => evaluate(changed), /no longer applicable/);
});
test('malformed, failed and unsupported audits fail closed', () => {
    for (const report of [{ error: { summary: 'Registry unavailable' } }, { auditReportVersion: 1 }, { auditReportVersion: 2, vulnerabilities: [] }]) {
        assert.throws(() => evaluate({ ...fixture(), report }), /unsupported or failed/);
    }
    for (const mutate of [
        data => { data.report.vulnerabilities.braces.via = []; },
        data => { data.report.vulnerabilities.braces.via = ['missing']; },
        data => { data.report.vulnerabilities.braces.via = ['tailwindcss']; },
        data => { delete data.lock.packages['node_modules/braces']; },
        data => { data.report.vulnerabilities.braces.severity = 'unknown'; },
        data => { data.report.vulnerabilities.braces.via = [null]; },
    ]) {
        const data = fixture();
        mutate(data);
        assert.throws(() => evaluate(data));
    }
});
test('exceptions require a unique advisory, valid calendar deadline and rationale', () => {
    for (const mutate of [
        data => data.exceptions.push({ ...data.exceptions[0] }),
        data => { data.exceptions[0].review_by = '2026-02-30'; },
        data => { data.exceptions[0].reason = ''; },
        data => { data.exceptions[0].advisory = 'braces'; },
    ]) {
        const data = fixture();
        mutate(data);
        assert.throws(() => evaluate(data), /Invalid or duplicate/);
    }
});
test('without exceptions, high findings block while moderate findings do not', () => {
    const data = fixture();
    data.exceptions = [];
    assert(evaluate(data).blocking.length > 0);
    Object.values(data.report.vulnerabilities).forEach(finding => { finding.severity = 'moderate'; });
    assert.deepEqual(evaluate(data).blocking, []);
});
