"""Held-out eval set: unseen wording + multi-step recovery.

Written after the main suite and the fine-tuning data existed, to test generalisation rather than familiarity:
  * unseen_wording: the main suite's intents, phrased fresh. `evals/check_heldout.py` verifies every utterance is
    far (fuzzy similarity < 70) from all fine-tuning sentences and all main-suite sentences.
  * recovery: the first attempt hits an obstacle (out of stock, unavailable size, ambiguity, empty cart, unknown
    product, a user correction) and the agent must recover, often across turns.

Same expectation keys as evals/tasks.py, plus a task-level option:
  out_of_stock: ["Product name"] (all sizes) or [("Product name", "size label")], set to stock 0 for the task only.
"""

HO: list[dict] = []


def task(id, category, turns, user=1, setup_cart=None, out_of_stock=None):
    HO.append({"id": id, "category": category, "user": user, "setup_cart": setup_cart or [], "turns": turns,
               "out_of_stock": out_of_stock or []})


def say(msg, **expect):
    return {"say": msg, "expect": expect}


MILK = [{"name": "Amul Toned Milk", "label": "1 L", "qty": 1}]
BASKET = [{"name": "Amul Toned Milk", "label": "1 L", "qty": 2}, {"name": "Britannia Brown Bread", "label": "400 g", "qty": 1},
          {"name": "Lay's Classic Salted Potato Chips", "label": "52 g", "qty": 3}]
UNAVAILABLE = ["out of stock", "unavailable", "sold out", "not available", "isn't available", "not in stock", "no stock"]

# ---------------- unseen wording (24) ----------------
task("ho-nav-0", "unseen_wording", [say("let's see what's sitting in my trolley", nav="/cart")], setup_cart=MILK)
task("ho-nav-1", "unseen_wording", [say("I'm ready to pay, bring up that screen", nav="/checkout", no_confirm=True)], setup_cart=MILK)
task("ho-nav-2", "unseen_wording", [say("let me look at everything I've ordered before", nav="/orders")])

task("ho-ord-0", "unseen_wording", [say("what did I end up getting on friday last week?", nav="/orders/{fri_order}")])
task("ho-ord-1", "unseen_wording", [say("find that january 5th receipt for me", nav="/orders/{jan5_order}")])
task("ho-ord-2", "unseen_wording", [say("anything I bought during september?", orders={"title_contains": "September 2026"})])
task("ho-ord-3", "unseen_wording", [say("have I ever picked up spinach?", tools=["find_orders"], orders={"title_contains": "spinach"})])

task("ho-srch-0", "unseen_wording", [say("is there anything sugar-free I can munch on?", products={"all_tag": "sugar-free", "min_count": 2})])
task("ho-srch-1", "unseen_wording", [say("cheap crisps please, nothing over 30 bucks", products={"all_max_price": 30, "any_subcategory": "Chips & Crisps"})])
task("ho-srch-2", "unseen_wording", [say("got anything for making pizza at home?", products={"any_name": "mozzarella"}, no_tools=["add_to_cart"])])
task("ho-srch-3", "unseen_wording", [say("which rice do people rate highest?", products={"first_subcategory": "Rice"})])
task("ho-srch-4", "unseen_wording", [say("looking for stuff that's packed with protein", products={"all_tag": "high-protein", "min_count": 3})])

task("ho-add-0", "unseen_wording", [say("could you chuck a couple of maggi packets into my order", cart=[{"name": "Maggi Masala Instant Noodles", "qty": 2}])])
task("ho-add-1", "unseen_wording", [say("I've run out of eggs, sort me out", cart=[{"subcategory": "Eggs"}])])
task("ho-add-2", "unseen_wording", [say("toss in a litre of amul toned milk", cart=[{"name": "Amul Toned Milk", "label": "1 L"}])])
task("ho-add-3", "unseen_wording", [say("grab the large tub of amul vanilla ice cream", cart=[{"name": "Amul Vanilla Ice Cream Tub", "label": "1.5 L"}])])
task("ho-add-4", "unseen_wording", [say("same flour I always get, one bag", cart=[{"name": "Aashirvaad Whole Wheat Atta"}])], user=2)
task("ho-add-5", "unseen_wording", [say("stick four red bulls in there", cart=[{"name": "Red Bull", "qty": 4}])])

task("ho-edit-0", "unseen_wording", [say("ditch the crisps", cart_not=["Lay's"])], setup_cart=BASKET)
task("ho-edit-1", "unseen_wording", [say("bump the milk up to four", cart=[{"name": "Amul Toned Milk", "qty": 4}])], setup_cart=BASKET)
task("ho-edit-2", "unseen_wording", [say("I want two loaves of the bread, not one", cart=[{"name": "Brown Bread", "qty": 2}])], setup_cart=BASKET)

task("ho-multi-0", "unseen_wording", [say("chuck in some brown bread and then head to the payment page", cart=[{"name": "Brown Bread"}], nav="/checkout", no_confirm=True)])
task("ho-multi-1", "unseen_wording", [say("do last friday's shop again", tools=["reorder"], cart=[{"name": "Eggoz Brown Eggs"}, {"name": "Spinach"}])])
task("ho-multi-2", "unseen_wording", [say("milk please, and have it turn up tomorrow evening", cart=[{"subcategory": "Milk"}], slot_hour=18)])

# ---------------- multi-step recovery (16) ----------------
task("ho-rec-0", "recovery", [say("i'd like some amul butter please", cart_unchanged=True, reply_mentions=UNAVAILABLE)],
     out_of_stock=["Amul Salted Butter"])
task("ho-rec-1", "recovery", [say("i'd like some amul butter please", cart_unchanged=True, reply_mentions=UNAVAILABLE),
                              say("ok, the mother dairy one then", cart=[{"name": "Mother Dairy Salted Butter"}])],
     out_of_stock=["Amul Salted Butter"])
task("ho-rec-2", "recovery", [say("i need fortune's sunflower oil, the 5 litre can", cart_unchanged=True),
                              say("fine, give me the 1 litre one", cart=[{"name": "Fortune Sunflower Oil", "label": "1 L"}])],
     out_of_stock=[("Fortune Sunflower Oil", "5 L")])
task("ho-rec-3", "recovery", [say("some cheese plus a loaf of brown bread please", cart=[{"name": "Brown Bread"}], cart_not=["Cheese"]),
                              say("the amul mozzarella", cart=[{"name": "Amul Mozzarella Cheese"}, {"name": "Brown Bread"}])])
task("ho-rec-4", "recovery", [say("get me everything from my friday order again", cart=[{"name": "Spinach"}, {"name": "Aloo Bhujia"}],
                                  cart_not=["Eggoz Brown Eggs"], reply_mentions=["egg"])],
     out_of_stock=["Eggoz Brown Eggs"])
task("ho-rec-5", "recovery", [say("let's wrap up and head to the till"),
                              say("oh right, add 2 packs of bananas then", cart=[{"name": "Banana", "qty": 2}]),
                              say("now checkout", nav="/checkout", no_confirm=True)])
task("ho-rec-6", "recovery", [say("add dragon fruit", cart_unchanged=True)])
task("ho-rec-7", "recovery", [say("can i get a loaf of britania brwn bred", cart=[{"name": "Britannia Brown Bread"}])])
task("ho-rec-8", "recovery", [say("one amul toned milk for me, cheers", cart=[{"name": "Amul Toned Milk"}]),
                              say("sorry, I meant amul full cream", cart=[{"name": "Amul Full Cream Milk"}], cart_not=["Amul Toned Milk"])])
task("ho-rec-9", "recovery", [say("we're running low on rice at home", cart_unchanged=True),
                              say("india gate basmati", cart=[{"name": "India Gate Basmati Rice"}])])
task("ho-rec-10", "recovery", [say("add bread, eggs and amul butter", cart=[{"subcategory": "Bread"}, {"subcategory": "Eggs"}],
                                   reply_mentions=["butter"])],
     out_of_stock=["Amul Salted Butter"])
task("ho-rec-11", "recovery", [say("is a 2 am drop-off possible?", slot_hour=6, reply_mentions=["6"]),
                               say("then make it 8 pm instead", slot_hour=20)], setup_cart=MILK)
task("ho-rec-12", "recovery", [say("finish up and submit this order", confirm="place_order"),
                               say("hold on, throw a brown loaf in before that", cart=[{"name": "Brown Bread"}]),
                               say("ok now place it", confirm="place_order")], setup_cart=MILK)
task("ho-rec-13", "recovery", [say("stock me up with three cans of red bull", cart=[{"name": "Red Bull", "qty": 3}]),
                               say("that's too many, just one", cart=[{"name": "Red Bull", "qty": 1}])])
task("ho-rec-14", "recovery", [say("do you stock any organic ice-cream?", no_tools=["add_to_cart"], cart_unchanged=True,
                                   reply_mentions=["couldn't", "no ", "not find", "don't have", "0"])])
task("ho-rec-15", "recovery", [say("add the cheapest kitkat", cart_unchanged=True)])

HELDOUT = HO
