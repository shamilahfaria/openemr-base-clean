# Railway deployment image for OpenEMR.
#
# Railway builds one service per Dockerfile (there is no docker-compose on the
# platform), so this image is just a thin wrapper around the official OpenEMR
# release image. The companion MySQL/MariaDB database is provisioned as a
# separate Railway service and wired in through environment variables (see
# docker/railway/README.md).
#
# Pinned by digest to match docker/production/docker-compose.yml so a Railway
# deploy is byte-for-byte the same application as the reference production
# stack. Bump both together.
FROM openemr/openemr:latest@sha256:faf0b8386895ed7ab1891bb7c94012e50a91917113e3ae7111b5287d28546b38

# The official image serves HTTP on 80 and HTTPS (self-signed) on 443. Railway
# terminates TLS at its edge and routes to the container's HTTP port, so we
# advertise 80 as the port Railway should send traffic to.
EXPOSE 80

# Clinical Co-Pilot in-chart launcher: two new self-contained library files
# plus a one-line require injected into the stock patient-summary page. Only
# NEW files are copied — overlaying the fork's whole demographics.php would be
# unsafe because the fork has diverged from this release image. The sed keeps
# the stock file otherwise byte-identical (</body> occurs exactly once); the
# grep fails the build loudly if the anchor ever vanishes.
# The php -l checks make a parse error fail the BUILD (the previous deploy
# keeps serving) instead of fataling the live patient chart.
COPY library/copilot.php library/copilot_launcher.php /var/www/localhost/htdocs/openemr/library/
# COPY leaves the files root-owned. The base image's boot script chmods every
# file to 400 but never chowns, and httpd workers run as `apache` — so anything
# not apache-owned becomes unreadable at runtime (fatal on the live chart).
# Match the stock files exactly: apache-owned, mode 400.
RUN cd /var/www/localhost/htdocs/openemr \
    && chown apache:root library/copilot.php library/copilot_launcher.php \
    && chmod 400 library/copilot.php library/copilot_launcher.php \
    && sed -i 's#</body>#<?php require_once(__DIR__ . "/../../../library/copilot_launcher.php"); ?></body>#' \
        interface/patient_file/summary/demographics.php \
    && grep -q 'copilot_launcher' interface/patient_file/summary/demographics.php \
    && php -l library/copilot.php \
    && php -l library/copilot_launcher.php \
    && php -l interface/patient_file/summary/demographics.php

# Clear a stale leader lock before starting.
#
# openemr.sh elects a "leader" container by creating sites/docker-leader with a
# no-clobber write and deleting it via an exit trap. When the container is
# killed rather than shut down gracefully (crash, OOM, redeploy) the trap never
# runs, so the lock file survives on the persistent Railway volume. On the next
# boot this image version tries to recreate it, fails fatally
# ("can't create .../docker-leader: File exists"), and the service crash-loops.
#
# We run a single replica, so this container is always the rightful leader:
# removing any orphaned lock at boot is safe. The install-complete marker
# (sites/docker-completed) is left untouched, so an already-provisioned volume
# is not reinstalled. The base image has no ENTRYPOINT and its CMD is
# ["./openemr.sh"] with WORKDIR /var/www/localhost/htdocs/openemr, which this
# reproduces via the shell.
CMD ["/bin/sh", "-c", "rm -f sites/docker-leader; exec ./openemr.sh"]
