"""What the live bot would reply to the exact queries from the transcript.

The sandbox has no internet, so this monkeypatches the fetcher with a
reconstruction of the real sources: the Yorkshire article's History section
(the Tostig line, verbatim), the listicle debris DuckDuckGo served for
"Yorkshire united kingdom", the North Yorkshire listicle heading, the
truncated Mexican Train rule, and the camera-question sources. The engine
(filtering, pooling, rotation, grounding) is the exact code pushed to the
branch - nothing here is simulated but the network.
"""
import funfacts
import llm

# ---- reconstructed sources ------------------------------------------------

YORKSHIRE_CAPPED = (
    "Yorkshire is a historic county in Northern England and the largest by "
    "area in the United Kingdom. Within the borders of the historic county "
    "of Yorkshire are large stretches of unspoiled countryside, including "
    "the Yorkshire Dales and the North York Moors.")
YORKSHIRE_FULL = YORKSHIRE_CAPPED + (
    "\n\nThe people of Yorkshire played a central role in the Wars of the "
    "Roses, the dynastic civil wars between the House of York and the House "
    "of Lancaster. At the Battle of Stamford Bridge in 1066, Tostig and "
    "Hardrada were both killed and their army was defeated decisively. "
    "Yorkshire was later the heartland of England's wool trade, which made "
    "Leeds and Bradford wealthy mill towns. Yorkshire contains two national "
    "parks and three areas of outstanding natural beauty.")
DALES = ("The Yorkshire Dales is an upland area of the Pennines in Northern "
         "England. The area holds some of the finest examples of karst "
         "limestone scenery in Britain, including caves and limestone "
         "pavements.")
HISTORY = ("The history of Yorkshire begins after the retreat of the ice "
           "age around 10,000 BC. In September 1066 Harold Godwinson marched "
           "north and met Harald Hardrada at Stamford Bridge, where Tostig "
           "and Hardrada were both killed and their army was defeated "
           "decisively. The Harrying of the North that followed devastated "
           "much of Yorkshire.")
HUMBER = ("Yorkshire and the Humber is one of the nine official regions of "
          "England. It comprises most of Yorkshire plus North and North "
          "East Lincolnshire.")
NY_CAPPED = ("North Yorkshire is a ceremonial county in Northern England. "
             "It is the largest ceremonial county in England by area, and "
             "includes the Yorkshire Dales and most of the North York Moors.")
NY_FULL = NY_CAPPED + (
    "\n\nRievaulx Abbey, one of the great Cistercian abbeys of England, "
    "stands in ruins in the county. North Yorkshire was formed in 1974 and "
    "covers most of the historic county of Yorkshire.")
TRAIN_CAPPED = (          # cut mid-parenthesis, exactly like the live source
    "Mexican Train is a dominoes game for 2 to 14 players, most commonly "
    "played with a double-twelve set. Each player builds a personal train "
    "of dominoes from a central hub. Typically, there is only one Mexican "
    "Train per round; rules vary on when it can be started (some say it can "
    "be started only after the opening turns are complete")
TRAIN_FULL = TRAIN_CAPPED + (
    ", while others allow it at any time). The game's popularity grew in "
    "the 1990s, when it was marketed with a battery-operated train-shaped "
    "hub that clacks when a player may start the Mexican Train. A marker "
    "such as a penny is placed on a player's train to signal that others "
    "may play on it.")
LENS = ("A wide-angle lens has a focal length shorter than a normal lens. "
        "Perspective distortion makes objects close to the camera appear "
        "larger, which is why the nose stretches in a selfie, where the "
        "lens sits close to the face.")
DDG_CAMERA = ("It is entirely psychological if you think a photo of you "
               "looks far worse than your reflection. The mere-exposure "
               "effect makes people prefer familiar images, such as their "
               "own mirror image.")
DDG_LISTICLE = ("Yorkshire is the largest county in the UK \u00b7 2.")

SEARCHES = {
    "yorkshire": ["Yorkshire", "Yorkshire Dales", "History of Yorkshire",
                  "Yorkshire and the Humber"],
    "yorkshire united kingdom": ["Yorkshire", "Yorkshire and the Humber",
                                 "History of Yorkshire"],
    "north yorkshire england": ["North Yorkshire"],
    "north yorkshire": ["North Yorkshire"],
    "mexican train": ["Mexican Train"],
    "look fatter camera": ["Wide-angle lens"],
}
CAPPED = {
    "Yorkshire": YORKSHIRE_CAPPED,
    "Yorkshire Dales": DALES,
    "History of Yorkshire": HISTORY,
    "Yorkshire and the Humber": HUMBER,
    "North Yorkshire": NY_CAPPED,
    "Mexican Train": TRAIN_CAPPED,
    "Wide-angle lens": LENS,
}
FULL = {
    "Yorkshire": YORKSHIRE_FULL,
    "North Yorkshire": NY_FULL,
    "Mexican Train": TRAIN_FULL,
}


def serve(url, params, timeout=8.0):
    if "wikipedia.org" in url:
        if params.get("list") == "search":
            hits = SEARCHES.get((params.get("srsearch") or "").lower(), [])
            return {"query": {"search": [{"title": t} for t in hits]}}
        titles = [t for t in (params.get("titles") or "").split("|") if t]
        if params.get("exchars"):            # the capped search extract
            pages = [{"title": t, "extract": CAPPED.get(t, "")} for t in titles]
            return {"query": {"pages": pages}}
        return {"query": {"pages": [          # the deep full-article refetch
            {"title": titles[0], "extract": FULL.get(titles[0], "")}]}}
    if "duckduckgo" in url:
        q = (params.get("q") or "").lower()
        if "camera" in q:
            return {"AbstractText": DDG_CAMERA, "Heading": "",
                    "RelatedTopics": []}
        if "yorkshire" in q:
            return {"AbstractText": DDG_LISTICLE,
                    "Heading": "Yorkshire", "RelatedTopics": []}
        return {"AbstractText": "", "RelatedTopics": []}
    return []                                  # geocoder: no such place


funfacts._http_get_json = serve
llm.is_configured = lambda o: True


def model_answer(question, sources, cfg):
    """Stands in for the channel's model: answers only from its sources."""
    if "camera" in question:
        return ("The lens sits close to the face in a selfie, and "
                "perspective distortion makes the nose appear larger; the "
                "mere-exposure effect then makes the mirror image feel "
                "like the true one.")
    return "NOTHING RELIABLE"


llm.answer_question = model_answer

OPTS = {"llm_api_key": "k", "max_fact_chars": 200,
        "answer_questions": True}


def bot(nick, arg):
    r = funfacts.get_funfact(arg, dict(OPTS))
    if not r or not r.get("fact"):
        return f'@{nick} couldn\'t find any fun facts for "{arg}" \U0001f615'
    return f"FunFact | {r['place']}: {r['fact']}"


funfacts._cache.clear()
print("hollieburgin: !funfact Yorkshire")
print("TruckingWithDocBot:", bot("hollieburgin", "Yorkshire"), "\n")
print("Hardclaws: !funfact Yorkshire            (asked again 1 min later)")
print("TruckingWithDocBot:", bot("Hardclaws", "Yorkshire"), "\n")
print("Hardclaws: !funfact Yorkshire united kingdom")
print("TruckingWithDocBot:", bot("Hardclaws", "Yorkshire united kingdom"), "\n")
print("hollieburgin: !funfact North Yorkshire England")
print("TruckingWithDocBot:", bot("hollieburgin", "North Yorkshire England"), "\n")
print("Hardclaws: !funfact why do look fatter on camera?")
print("TruckingWithDocBot:",
      bot("Hardclaws", "why do look fatter on camera?"), "\n")
print("hollieburgin: !funfact Mexican train")
print("TruckingWithDocBot:", bot("hollieburgin", "Mexican train"), "\n")

# What DuckDuckGo's listicle now yields if it is ever the source that answers
# (it no longer is for these - Wikipedia is asked first - but if Wikipedia
# is unreachable, this is what would post).
funfacts._cache.clear()
ddg = funfacts._duckduckgo("yorkshire united kingdom")
print("DuckDuckGo listicle, cleaned:",
      ddg["facts"][0] if ddg else "(nothing usable)")
