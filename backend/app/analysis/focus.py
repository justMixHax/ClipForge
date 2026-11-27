"""What you are looking for: the kinds of moment ClipForge can be asked to find in a video.

Each kind says, in words Claude is given as they stand: what counts as one, what has to be inside the clip for it
to hold up, what looks like one but is not, and how such a clip should open. The local rules only know which of
their own categories belong to a kind, so without Claude a kind is an approximation, and the clips say so.
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT = "best"
LOCAL_BONUS, LOCAL_PENALTY = 8.0, 12.0   # the local rules: points for a moment of the kind asked for, off the others


@dataclass(frozen=True)
class Focus:
    key: str
    label: str                       # on the button
    hint: str                        # one line under it
    categories: tuple[str, ...]      # the clip categories its moments take; the usual one first
    looking_for: str = ""            # what counts as one
    proof: str = ""                  # what has to be inside the clip
    not_this: str = ""               # what looks like one but is not
    hook: str = ""                   # how such a clip should open
    led_by: str = "words"            # "words" or "picture": what mostly carries a moment of this kind


FOCUSES: dict[str, Focus] = {kind.key: kind for kind in (
    Focus("best", "Best moments", "Whatever is strongest in the video, of any kind.",
          ("highlight", "funny", "hype", "fail", "rage", "hot_take", "debate", "story", "surprise", "reaction",
           "action", "scare", "wholesome", "tip")),
    Focus("funny", "Funny moments", "Jokes that land, banter, fails and absurd situations.",
          ("funny", "fail", "reaction", "surprise"),
          looking_for="Moments that make a stranger laugh: a joke that lands, deadpan banter, a comeback, an absurd "
                      "situation, a fail with a reaction, a running gag paying off.",
          proof="You can quote the punchline or name the thing that happens, and the setup it needs is inside the "
                "clip. Laughter, a stunned pause or a loud reaction right after it is a good sign.",
          not_this="People laughing about something that happened earlier; an inside joke that needs the rest of "
                   "the video; someone only saying that something is funny; shouting with no joke in it.",
          hook="Open on the last line of setup the joke needs, as late as possible, so the punchline is clearly on "
               "its way. Never open on laughter left over from an earlier joke."),
    Focus("gameplay", "Gameplay showcase", "Clean, skilful or smooth play that is worth watching for itself.",
          ("action", "hype", "highlight"),
          looking_for="Play worth watching for itself: a clean run, a skilful or smooth sequence, a smart trick, a "
                      "satisfying build or combo. The picture carries it; talk is optional.",
          proof="Something visibly happens from start to finish: the loud-or-busy list shows action there, or the "
                "lines around it describe the play as it happens. It has a clear beginning and a result.",
          not_this="Menus, loading screens, walking with nothing happening, long talk over idle play, a moment "
                   "that only makes sense with the minutes before it.",
          hook="Open as the action starts or one beat before it, with no warm-up, and end on the result.",
          led_by="picture"),
    Focus("hype", "Hype / clutch", "Big plays, close wins and comebacks.",
          ("hype", "action", "reaction"),
          looking_for="A big play: a clutch, a close win, a comeback, a rare drop, a record, a moment where "
                      "everything is at stake and it works out, or fails spectacularly.",
          proof="The stakes can be heard or seen before the result (last life, one against three, final seconds), "
                "and the result is inside the clip, with the reaction to it.",
          not_this="Ordinary kills or wins; shouting with no result; excitement about something that is not shown.",
          hook="Open where the stakes become clear. The on-screen line names the stakes, never the result.",
          led_by="picture"),
    Focus("horror", "Horror / scares", "Jump scares, chases and real dread.",
          ("scare", "reaction", "surprise"),
          looking_for="A scare or a stretch of real dread: a jump scare with the scream, a chase, something "
                      "appearing where it should not, the player going quiet and tense before it happens.",
          proof="A sudden loud moment after a quiet stretch (see the loud-or-busy list), a scream or a startled "
                "line, or lines that show fear building. The quiet before the scare is part of the clip.",
          not_this="Calm exploring with nothing happening; talking about a scary game; a scare with its build-up "
                   "cut off.",
          hook="Open in the quiet two to four seconds before the scare so the tension is felt, or on the line that "
               "shows the fear. The on-screen line teases and never gives the scare away.",
          led_by="picture"),
    Focus("rage", "Rage moments", "Frustration boiling over, with what set it off.",
          ("rage", "fail", "reaction"),
          looking_for="Real frustration boiling over: rage at an unfair death, a rant, a slammed desk, a rage "
                      "quit, an argument with the game or a teammate.",
          proof="What set it off is inside the clip (it can be seen or it is said), and the anger is real and "
                "builds: a raised voice, swearing, words repeated, a loud peak.",
          not_this="Mild annoyance; shouting from excitement; anger with its cause missing; a rant aimed at a "
                   "real person that would only spread abuse.",
          hook="Open on what sets it off, or a second or two before it. The on-screen line names the cause."),
    Focus("streamer", "Streamer moments", "Reactions, chat and unplanned moments on stream.",
          ("reaction", "funny", "story", "surprise"),
          looking_for="The streamer being themselves: a big reaction, a back-and-forth with chat or a donation, an "
                      "awkward or unplanned moment, something going wrong on stream.",
          proof="The streamer's own words or reaction carry it, and what they react to is inside the clip: read "
                "out, shown or said.",
          not_this="Reading chat with no reaction; reacting to something that is never seen or heard; routine "
                   "thank-yous for subscriptions.",
          hook="Open on the thing they react to, or on their first surprised line."),
    Focus("wholesome", "Wholesome / cosy", "Kindness, warmth and calm that feels good.",
          ("wholesome", "story", "reaction"),
          looking_for="Warmth: kindness, a generous or caring gesture, a heartfelt thank-you, friends being good "
                      "to each other, a calm cosy stretch that feels good to watch.",
          proof="The kind act or warm line is inside the clip and is sincere: who does what for whom is clear.",
          not_this="Sarcasm; a joke at someone's expense; a donation read out with no feeling; calm stretches "
                   "where nothing is felt.",
          hook="Open on the gesture or the line that starts it. The on-screen line is plain and warm, never "
               "clickbait."),
    Focus("educational", "Educational", "Tips, how-tos and explanations people can use.",
          ("tip", "story", "hot_take"),
          looking_for="Something to use or learn: a tip, a how-to step, a clear explanation, a mistake to avoid, a "
                      "surprising fact with its reason.",
          proof="The takeaway is complete inside the clip: the question or problem, then the answer. Someone "
                "could repeat what they learned in one sentence.",
          not_this="A promise of a tip that comes later; an explanation that needs something the clip never "
                   "shows; general chat about a topic with no takeaway.",
          hook="Open on the problem, the question or the surprising claim. The on-screen line says what there is "
               "to learn."),
    Focus("story", "Stories & hot takes", "A short story with a turn, or an opinion people argue with.",
          ("story", "hot_take", "debate"),
          looking_for="A short story with a turn or a punchline, or a strong opinion people would argue with.",
          proof="For a story: its start, its turn and its ending are inside the clip. For an opinion: the claim is "
                "said outright, with its reason.",
          not_this="A story that trails off; an opinion hedged until it says nothing; an argument where only one "
                   "side is heard.",
          hook="Open on the line that sets the stakes or states the claim."),
)}


def get(key: object) -> Focus:
    """The kind with this key; "Best moments" for anything else."""
    return FOCUSES.get(key if isinstance(key, str) else "", FOCUSES[DEFAULT])


def brief(kind: Focus) -> str:
    """What Claude is told about the kind asked for. Nothing when any kind of moment will do."""
    if kind.key == DEFAULT:
        return ""
    return (f"The user wants one kind of moment from this video: {kind.label}.\n"
            f"- What counts: {kind.looking_for}\n"
            f"- It must hold up: {kind.proof}\n"
            f"- Leave out: {kind.not_this}")


def local_score(score: float, category: str, kind: Focus) -> float:
    """A local score, lifted when the local rules read the moment as the kind asked for and lowered when not."""
    if kind.key == DEFAULT:
        return score
    change = LOCAL_BONUS if category in kind.categories else -LOCAL_PENALTY
    return round(min(100.0, max(0.0, score + change)), 1)
