"""Publishing: connected accounts, the post queue and scheduler, and one adapter per platform.

Every adapter uses the platform's official API and sign-in, and a post only counts as published once the
platform has confirmed it.
"""
