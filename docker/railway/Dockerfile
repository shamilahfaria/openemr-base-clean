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
