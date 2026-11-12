"""CUDA path tracing of PBR surfaces and Gaussian radiance fields together.

Gaussian opacity and SH evaluation follow Mitsuba's volprim_rf_basic model.
Mesh BSDF sampling uses next event estimation and multiple importance sampling.
Gaussian traversal does not consume a surface bounce. The Gaussian representation
contains baked radiance; relighting it requires additional material information.
The optional "gaussian_gain" property rescales that baked radiance so the splat
brightness matches the level the surrounding PBR surfaces receive from the scene
lights. This is exposure matching, not physical relighting, and is recorded in
the render metadata.
"""
import drjit as dr
import mitsuba as mi
from mitsuba.ad.integrators.volprim_rf_basic import BasicVolumetricPrimitiveRadianceFieldIntegrator

_field = BasicVolumetricPrimitiveRadianceFieldIntegrator(mi.Properties())


@dr.syntax
def visibility(scene, ray, active):
    value = mi.Float(1)
    active = mi.Bool(active)
    count = mi.UInt32(0)
    while dr.hint(active, max_iterations=256, label="Gaussian shadow transmittance"):
        si = scene.ray_intersect(ray, active=active)
        hit = active & si.is_valid()
        gaussian = hit & si.shape.is_ellipsoids()
        trans = _field.eval_transmission(si, ray, gaussian)
        value = dr.select(gaussian, value * trans, dr.select(hit, 0, value))
        offset = si.t + 1e-5
        ray.o = dr.select(gaussian, ray(offset), ray.o)
        ray.maxt = dr.select(gaussian, dr.maximum(0, ray.maxt - offset), ray.maxt)
        count += 1
        active = gaussian & (value > 1e-4) & (count < 256) & (ray.maxt > 0)
    return value


def mis_weight(a, b):
    aa, bb = dr.square(a), dr.square(b)
    return dr.select(aa + bb > 0, aa / (aa + bb), 0)


class HybridPathIntegrator(mi.SamplingIntegrator):
    def __init__(self, props):
        super().__init__(props)
        self.max_depth = int(props.get("max_depth", 8))
        self.max_events = int(props.get("max_events", 512))
        self.gaussian_gain = float(props.get("gaussian_gain", 1.0))

    @dr.syntax
    def sample(self, scene, sampler, ray, medium=None, active=True):
        ray = mi.Ray3f(ray)
        active = mi.Bool(active)
        result = mi.Spectrum(0)
        throughput = mi.Spectrum(1)
        depth = mi.UInt32(0)
        events = mi.UInt32(0)
        previous = dr.zeros(mi.SurfaceInteraction3f)
        previous_pdf = mi.Float(1)
        previous_delta = mi.Bool(True)
        valid = mi.Bool(False)
        context = mi.BSDFContext()
        while dr.hint(active, max_iterations=self.max_events, label="A100 mesh and Gaussian paths"):
            si = scene.ray_intersect(ray, ray_flags=mi.RayFlags.All,
                                     coherent=(events == 0), active=active)
            hit = si.is_valid()
            gaussian = active & hit & si.shape.is_ellipsoids()
            surface = active & hit & ~gaussian
            valid |= active & hit

            # Gaussian SH radiance contributes to primary, reflected and refracted rays.
            transmission = _field.eval_transmission(si, ray, gaussian)
            emission = _field.eval_sh_emission(si, ray, gaussian)
            emission = mi.math.srgb_to_linear(emission) * self.gaussian_gain
            result += dr.select(gaussian, throughput * (1 - transmission) * emission, 0)

            # Visible environment and area lights, balanced with emitter sampling.
            ds_hit = mi.DirectionSample3f(scene, si=si, ref=previous)
            emitter_pdf = scene.pdf_emitter_direction(previous, ds_hit, active & ~previous_delta)
            weight = dr.select(previous_delta, 1, mis_weight(previous_pdf, emitter_pdf))
            result += dr.select(active & ~gaussian,
                                throughput * weight * si.emitter(scene).eval(si, active & ~gaussian), 0)

            bsdf = si.bsdf(ray)
            cont = surface & (depth + 1 < self.max_depth)
            direct = cont & mi.has_flag(bsdf.flags(), mi.BSDFFlags.Smooth)
            ds, light_weight = scene.sample_emitter_direction(si, sampler.next_2d(), False, direct)
            direct &= ds.pdf > 0
            shadow_ray = si.spawn_ray_to(ds.p)
            shadow_ray.d = dr.select(dr.isfinite(ds.dist), shadow_ray.d, ds.d)
            shadow_ray.maxt = dr.select(dr.isfinite(ds.dist), shadow_ray.maxt, dr.inf)
            trans_shadow = visibility(scene, shadow_ray, direct)
            bsdf_value, bsdf_pdf = bsdf.eval_pdf(context, si, si.to_local(ds.d), direct)
            mis = dr.select(ds.delta, 1, mis_weight(ds.pdf, bsdf_pdf))
            result += dr.select(direct, throughput * light_weight * bsdf_value * mis * trans_shadow, 0)

            bs, bsdf_weight = bsdf.sample(context, si, sampler.next_1d(), sampler.next_2d(), cont)
            bounce_ray = si.spawn_ray(si.to_world(bs.wo))
            pass_ray = mi.Ray3f(ray)
            pass_ray.o = si.p + ray.d * 1e-5
            ray = dr.select(gaussian, pass_ray, bounce_ray)
            throughput *= dr.select(gaussian, mi.Spectrum(transmission), bsdf_weight)
            previous = dr.select(surface, si, previous)
            previous_pdf = dr.select(surface, bs.pdf, previous_pdf)
            previous_delta = dr.select(surface, mi.has_flag(bs.sampled_type, mi.BSDFFlags.Delta), previous_delta)
            depth += dr.select(surface, 1, 0)
            events += 1
            rr_probability = dr.minimum(dr.max(throughput), 0.95)
            roulette = surface & (depth >= 4)
            throughput = dr.select(roulette, throughput / rr_probability, throughput)
            survives = ~roulette | (sampler.next_1d() < rr_probability)
            active &= hit & (gaussian | cont) & survives & (dr.max(throughput) > 1e-5)
            active &= events < self.max_events
        return result, valid, []


mi.register_integrator("a100_hybrid_path", lambda props: HybridPathIntegrator(props))
