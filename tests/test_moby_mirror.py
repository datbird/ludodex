"""The mirror exists so a paid walk survives a rebuild. These are the properties that
makes true: a durable cursor, a slim schema, and a merge that prefers the free pointers
already anchored over anything it could re-derive by name."""
import os, sys, json
PASS=[]
def check(l,c):
    PASS.append(c); print("  %s   %s" % ("ok " if c else "FAIL", l))
    if not c: sys.exit("FAILED: "+l)

REC = {"game_id": 1, "title": "The X-Files Game", "moby_score": 3.8,
       "description": "long prose we do not want to store",
       "alternate_titles": [{"title": "Salaiset Kansiot"}],
       "genres": [{"genre_category": "Basic Genres", "genre_name": "Adventure"},
                  {"genre_category": "Perspective", "genre_name": "1st-person"}],
       "platforms": [{"platform_id": 3, "platform_name": "Windows",
                      "first_release_date": "1998"},
                     {"platform_id": 6, "platform_name": "PlayStation",
                      "first_release_date": "1999"}],
       "sample_cover": {"image": "http://x/big.jpg"}}

def main():
    root=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0,root); sys.path.insert(0,os.path.join(root,"tests"))
    import test_support; test_support.isolate("ludodex-mobymirror-")
    sys.path.insert(0,os.path.join(root,"ludodex"))
    import config, moby_mirror as MM, mobygames as mg, matchindex as M

    print("1. the schema keeps IDENTITY and drops the bulk")
    con=MM.con_db(); MM.store(con,REC,1000,keep_payload=False); con.commit()
    g=con.execute("SELECT * FROM moby_games WHERE id=1").fetchone()
    check("title + norm_key", g["title"]=="The X-Files Game" and g["norm_key"])
    check("year is the EARLIEST across platforms, not the first listed", g["year"]==1998)
    check("score kept", abs(g["score"]-3.8)<0.01)
    check("only BASIC genres, not perspectives", g["genres"]=="Adventure")
    check("the description is NOT stored — it is most of the bytes", g["payload"] is None)
    check("one row per (game, platform)", con.execute(
        "SELECT COUNT(*) FROM moby_platforms WHERE game_id=1").fetchone()[0]==2)
    check("alternate titles kept — they are matching keys", con.execute(
        "SELECT COUNT(*) FROM moby_alt WHERE game_id=1").fetchone()[0]==1)

    print()
    print("2. the payload can be kept if you would rather spend disk than a second walk")
    MM.store(con,REC,1000,keep_payload=True); con.commit()
    check("payload stored on request", con.execute(
        "SELECT payload FROM moby_games WHERE id=1").fetchone()[0] is not None)
    check("but OFF by default", config.DEFAULTS["mobygames_store_payload"]=="0")
    check("re-storing the same game does not duplicate it", con.execute(
        "SELECT COUNT(*) FROM moby_games").fetchone()[0]==1)
    con.close()

    print()
    print("3. THE CURSOR IS DURABLE — 4.6 hours is long enough for something to happen")
    calls=[]
    def fake_games(offset=0,limit=100,fmt="normal",platform=None,**kw):
        calls.append((platform,offset))
        if platform==3 and offset==0: return [dict(REC,game_id=i) for i in range(1,101)]
        if platform==3 and offset==100: return [dict(REC,game_id=i) for i in range(101,151)]
        return []
    mg.games=fake_games; mg.platforms=lambda: [{"platform_id":3},{"platform_id":6}]
    config.set_("mobygames_api_key","k")
    r=MM.walk(progress=False)
    check("it walked both platforms: %s" % r["stopped"], r["stopped"]=="complete")
    check("150 games stored", r["games"]==150)
    s=MM.status()
    check("both platforms recorded done", s["platforms_done"]==2)
    calls.clear()
    r2=MM.walk(progress=False)
    check("a second run re-asks NOTHING — finished platforms are remembered",
          r2["requests"]==0 and not calls)

    print()
    print("4. a budget stop leaves a cursor that RESUMES, not one that restarts")
    con=MM.con_db(); con.execute("DELETE FROM state"); con.commit(); con.close()
    calls.clear()
    r3=MM.walk(max_requests=1, progress=False)
    check("stopped on budget", r3["stopped"]=="budget")
    s=MM.status()
    check("cursor points past what it did: platform %s offset %s"
          % (s["cursor_platform"], s["cursor_offset"]), int(s["cursor_offset"])==100)
    calls.clear()
    MM.walk(progress=False)
    check("the resume starts at the cursor, not at zero",
          calls and calls[0]==(3,100))

    print()
    print("5. a rate limit stops the walk instead of hammering through it")
    con=MM.con_db(); con.execute("DELETE FROM state"); con.commit(); con.close()
    def quota(*a,**k): raise mg.MobyError("quota","rate limited")
    mg.games=quota
    r4=MM.walk(progress=False)
    check("stopped on quota, not raised", r4["stopped"]=="quota")

    print()
    print("6. the index prefers the FREE pointers over anything it re-derives")
    src=open(os.path.join(root,"ludodex","matchindex.py"),encoding="utf-8").read()
    step=src[src.index("def _merge_moby"):src.index("def _match_moby")]
    check("an already-known moby id is skipped", "if gid in known" in step)
    check("and the reason is stated", "WORSE answer" in step)
    check("a miss mints its own identity in its own range", "MOBY_ID_BASE + int" in step)
    check("MOBY_ID_BASE is above every other range",
          M.MOBY_ID_BASE > M.TGDB_ID_BASE > M.LEARNED_ID_BASE > M.SS_ID_BASE)
    gate=src[src.index("def _match_moby"):src.index("def _merge_wikidata_ids")]
    check("the gate is the same one ScreenScraper uses", "matchgate.score" in gate)
    check("and hardware must agree when both sides state it",
          "ig.game_platforms" in gate)
    check("MobyGames is credited as a source, with its non-commercial terms",
          any(s["name"]=="MobyGames" and "non-commercial" in s["license"]
              for s in M.SOURCES))
    print()
    print("%d checks, all passed" % len(PASS))

main()
