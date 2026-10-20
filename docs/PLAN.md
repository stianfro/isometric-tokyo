# isometric.tokyo proof-of-concept plan

Status: proposed, not deployed. Start with the existing roughly 800 × 800 metre
Shinjuku sample and 2400 × 2400 renders. The first milestone is a public,
source-only viewer, not a citywide map or an AI rendering service.

## Architecture

```text
Offline: PLATEAU -> just / Python / Blender -> versioned static assets
Release: tested viewer + assets -> GHCR image digest -> lab deployment
Traffic: browser -> isometric.tokyo HTTPS -> existing ingress / Gateway
        -> ClusterIP Service -> static viewer container
```

Use a small non-root container with a read-only filesystem, health checks and
resource requests/limits. Include the viewer assets in the image. No backend,
database, PVC or rendering GPU is needed in the cluster. Start with one pod;
make two replicas an easy adjustment, not a prerequisite. Two replicas do not
protect against a home power or internet outage.

Keep public examples portable. `edge.example.net` is a placeholder for the
existing public entry point. Its exact value, lab configuration and credentials
belong in private deployment configuration, not this repository. Public DNS can
still reveal a direct home-hosting origin; a placeholder is not origin protection.

## Phase 1: freeze the baseline and build the viewer

- Preserve the original textured render and matching geometry-only render.
  Keep the differently framed detail image as a separate close-up.
- Provide drag/pinch/scroll navigation, keyboard controls, reset view, mode
  switching and a shareable URL containing image position and zoom.
- Show PLATEAU attribution, dataset edition, known mixed-LOD limitations and
  a clear "No AI styling" label. Offer the original-resolution image.
- Start with one image, not raster tiles. Measure initial download size and
  mobile loading before adding formats or infrastructure. Lazy-load comparison
  images; retain the original PNG for quality inspection.
- Record camera orientation, origin, pixel scale, lighting, render settings and
  asset version alongside the existing source manifests.

**Acceptance:** desktop and mobile navigation work; the geometry toggle stays
aligned; a shared view reopens correctly; landmarks and source defects can be
inspected; credits are visible; no console errors or third-party telemetry.

## Phase 2: package, release and expose HTTPS

- Keep development, build and verification commands in `justfile`. Run lints,
  tests and browser smoke checks before release. Validate future YAML with
  `yq`, then validate rendered Kubernetes resources against their schemas.
- Publish a versioned GHCR image and deploy its immutable digest. Build for the
  actual node architecture, or both AMD64 and ARM64 if needed. Keep large source
  archives and Blender files outside the runtime image and Git history.
- Prefer the lab's existing GitOps workflow. Otherwise use a documented manual
  release with a recorded digest and rollback command. CI does not need lab
  administrator credentials. Keep private overlays and secrets separate.
- Reuse the existing supported ingress controller or Gateway implementation;
  do not install another controller for this site. Configure the public host
  explicitly. DNS aliases do not change the browser's requested hostname, so
  routing and the TLS certificate must cover `isometric.tokyo`, not merely the
  infrastructure hostname. [Kubernetes Ingress documentation](https://kubernetes.io/docs/concepts/services-networking/ingress/)
- For the domain apex, use provider-supported ALIAS/ANAME or CNAME flattening
  toward `edge.example.net`; otherwise use A/AAAA records for the reachable
  address. Do not assume an ordinary apex CNAME works. An optional `www` CNAME
  needs its own host/certificate coverage and redirect to the apex.
  [Apex DNS options](https://developers.cloudflare.com/dns/manage-dns-records/how-to/create-zone-apex/)
- Reuse an existing cert-manager issuer where available. Choose HTTP-01 or
  DNS-01 according to reachable ports, DNS provider support and existing lab
  practice. Keep DNS tokens and certificate keys out of the repository. For
  Ingress, issuer annotations can connect host configuration to certificate
  issuance; use the supported equivalent for a Gateway-based lab.
  [cert-manager Ingress documentation](https://cert-manager.io/docs/usage/ingress/)

**Acceptance:** public DNS resolves correctly; HTTPS validates from outside the
lab; only the intended website is exposed; certificate renewal is configured;
health checks pass; a previous image digest can be restored. Cache versioned
assets while allowing the HTML entry point to update.

## Phase 3: assess quality, then choose a style

Review roofs, building identity, terrain alignment, floating geometry, texture
gaps and photographic shadows against the geometry-only baseline. Document
defects instead of hiding them. Try deterministic materials and lighting before
AI styling. Any AI experiment remains a separate version with a source comparison.

**Acceptance:** choose a visual direction based on recognizability and continuity,
not appearance alone. Preserve the unstyled reference and document distortions.

## Phase 4: expand one neighboring patch

Use one global projected pixel grid with fixed camera orientation and pixels per
metre. Include neighboring geometry and enough overscan for towers and shadows,
then crop rendered images. Cutting buildings at each image boundary creates
seams. Test one adjacent patch before increasing coverage substantially.

Introduce offline raster tile pyramids only when measured loading or image-size
limits justify them. Generate lower resolutions from the same master imagery.
This is an isometric image coordinate system, not automatically Web Mercator.

**Acceptance:** adjacent images join without broken buildings, missing geometry,
shadow jumps or scale changes. Dataset and style revisions remain reproducible.

## Decisions needed before deployment

- DNS provider and apex-alias support; exact target retained privately.
- Existing ingress/Gateway implementation, version and route conventions.
- cert-manager availability, issuer and supported challenge method.
- AMD64/ARM64 nodes; GitOps and registry image-pull arrangements.
- Available upload bandwidth, public reachability and acceptable outage risk.

The public service is read-only. Do not expose cluster APIs, dashboards, source
credentials or rendering controls. No telemetry by default. Citywide coverage,
live 3D, accounts, uploads, runtime AI, geocoding and accurate image-click-to-world
coordinates are outside the initial proof of concept.
