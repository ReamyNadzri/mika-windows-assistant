"""Personal assistant extension for the pinned Nous Hermes runtime."""

def register(ctx):
    from .plugin import register as register_plugin
    register_plugin(ctx)
