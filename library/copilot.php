<?php

/**
 * Clinical Co-Pilot launch helpers.
 *
 * Builds the URL that opens the external Clinical Co-Pilot sidecar (the FastAPI
 * agent in /copilot) from inside OpenEMR — a global launcher in the top nav and
 * a patient-context link on the summary screen. The base URL is configurable so
 * the same code works against local, staging, and the deployed environment.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Shamilah Faria <shamilahfaria@gmail.com>
 * @copyright Copyright (c) 2026 Shamilah Faria
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

use OpenEMR\Common\Uuid\UuidRegistry;

/**
 * Base URL of the Co-Pilot sidecar (no trailing slash).
 *
 * Resolution order: an OpenEMR global, then a COPILOT_BASE_URL env var, then the
 * deployed default.
 */
function copilotBaseUrl(): string
{
    $configured = $GLOBALS['copilot_base_url'] ?? getenv('COPILOT_BASE_URL');
    $base = is_string($configured) ? trim($configured) : '';
    if ($base === '') {
        $base = 'https://copilot-early-sub.up.railway.app';
    }
    return rtrim($base, '/');
}

/**
 * Resolve a patient's FHIR UUID string from their internal pid, or null when
 * the patient has no uuid recorded.
 */
function copilotPatientUuid(int $pid): ?string
{
    if ($pid <= 0) {
        return null;
    }
    $row = sqlQuery("SELECT uuid FROM patient_data WHERE pid = ?", [$pid]);
    if (empty($row['uuid'])) {
        return null;
    }
    return UuidRegistry::uuidToString($row['uuid']);
}

/**
 * Username of the demo/test admin account. The one-click "Generate demo token"
 * affordance mints a privileged admin token, so it is offered only to this
 * user. Configurable for non-default demo setups.
 */
function copilotDemoAdminUser(): string
{
    $configured = $GLOBALS['copilot_demo_admin_user'] ?? getenv('COPILOT_DEMO_ADMIN_USER');
    $user = is_string($configured) ? trim($configured) : '';
    return $user !== '' ? $user : 'admin';
}

/**
 * True when the given OpenEMR username is the demo admin — i.e. the only user
 * who should see the demo-token button.
 */
function copilotIsDemoAdmin(?string $username): bool
{
    return $username !== null && $username !== '' && $username === copilotDemoAdminUser();
}

/**
 * Build the Co-Pilot /ui launch URL. The bearer token is never placed in the
 * URL — the UI collects it separately (or mints a demo token in one click).
 *
 * When $demoAdmin is true the URL carries ``demo=1``, which is the only signal
 * that reveals the "Generate demo token" button in the Co-Pilot UI.
 */
function copilotLaunchUrl(?string $patientUuid = null, ?string $clinician = null, bool $demoAdmin = false): string
{
    $query = [];
    if (!empty($patientUuid)) {
        $query['patient'] = $patientUuid;
    }
    if (!empty($clinician)) {
        $query['clinician'] = $clinician;
    }
    if ($demoAdmin) {
        $query['demo'] = '1';
    }
    $url = copilotBaseUrl() . '/ui';
    if ($query !== []) {
        $url .= '?' . http_build_query($query);
    }
    return $url;
}
