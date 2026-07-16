<?php

/**
 * Clinical Co-Pilot in-chart launcher: a floating button on the patient
 * summary that opens the Co-Pilot in a modal iframe over the chart.
 *
 * Self-contained by design so the same file runs in two contexts:
 * the fork's demographics.php requires it directly, and the Railway image
 * injects the same require into the *official release* demographics.php at
 * build time (docker/railway/Dockerfile) — so it only uses core primitives
 * that exist in both (js_escape/attr/xlt, $_SESSION, sqlQuery). The modal is
 * hand-rolled rather than dlgopen() so nothing depends on dialog.js
 * internals or same-origin iframe access.
 *
 * A launch failure must never break the chart: everything is guarded and
 * degrades to rendering nothing.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Shamilah Faria <shamilahfaria@gmail.com>
 * @copyright Copyright (c) 2026 Shamilah Faria
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

try {
    require_once __DIR__ . '/copilot.php';

    $copilotLauncherPid = isset($pid) ? (int)$pid : (int)($_SESSION['pid'] ?? 0);
    $copilotLauncherUser = (string)($_SESSION['authUser'] ?? '');
    $copilotLauncherUrl = copilotLaunchUrl(
        copilotPatientUuid($copilotLauncherPid),
        $copilotLauncherUser,
        copilotIsDemoAdmin($copilotLauncherUser)
    );
} catch (\Throwable $copilotLauncherError) {
    // Boundary guard: the co-pilot is an optional add-on — log and vanish
    // rather than take down the patient chart.
    error_log('copilot launcher unavailable: ' . $copilotLauncherError->getMessage());
    return;
}
?>
<div id="copilot-fab" style="position:fixed; bottom:1.25rem; right:1.25rem; z-index:99990;">
    <button type="button" onclick="copilotModalOpen()"
            title="<?php echo attr(xl('Open the Clinical Co-Pilot for this patient')); ?>"
            style="background:#1f3a5f; color:#fdfcf9; border:0; border-radius:2rem;
                   padding:0.65rem 1.15rem; font-weight:600; font-size:0.9rem;
                   box-shadow:0 4px 14px rgba(28,39,51,.35); cursor:pointer;">
        <i class="fa fa-robot mr-1" aria-hidden="true"></i><?php echo xlt('Co-Pilot'); ?>
    </button>
</div>
<div id="copilot-modal" hidden
     style="position:fixed; inset:0; z-index:99991; background:rgba(28,39,51,.55);">
    <div style="position:absolute; top:3vh; left:50%; transform:translateX(-50%);
                width:min(980px, 94vw); height:92vh; background:#f7f5f0;
                border-radius:0.5rem; box-shadow:0 12px 40px rgba(0,0,0,.4);
                display:flex; flex-direction:column; overflow:hidden;">
        <div style="display:flex; align-items:center; gap:0.75rem; padding:0.5rem 1rem;
                    background:#1f3a5f; color:#fdfcf9;">
            <strong style="font-size:0.95rem;"><?php echo xlt('Clinical Co-Pilot'); ?></strong>
            <a href="<?php echo attr($copilotLauncherUrl); ?>" target="_blank" rel="noopener"
               style="margin-left:auto; color:#cdd9ea; font-size:0.8rem;"><?php echo xlt('Open in new tab'); ?></a>
            <button type="button" onclick="copilotModalClose()"
                    aria-label="<?php echo attr(xl('Close')); ?>"
                    style="background:none; border:0; color:#fdfcf9; font-size:1.4rem;
                           line-height:1; cursor:pointer;">&times;</button>
        </div>
        <iframe id="copilot-frame" title="<?php echo attr(xl('Clinical Co-Pilot')); ?>"
                data-src="<?php echo attr($copilotLauncherUrl); ?>"
                style="flex:1; border:0; width:100%;"></iframe>
    </div>
</div>
<script>
    // Lazy-load the iframe on first open so the chart never pays the cost.
    function copilotModalOpen() {
        var frame = document.getElementById('copilot-frame');
        if (!frame.getAttribute('src')) {
            frame.setAttribute('src', frame.getAttribute('data-src'));
        }
        document.getElementById('copilot-modal').hidden = false;
    }
    function copilotModalClose() {
        document.getElementById('copilot-modal').hidden = true;
    }
    document.getElementById('copilot-modal').addEventListener('click', function (event) {
        if (event.target === this) {
            copilotModalClose();     // click on the backdrop closes
        }
    });
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') {
            copilotModalClose();
        }
    });
</script>
