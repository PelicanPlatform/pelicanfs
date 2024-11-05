def parse_metalink(headers: dict[str, str]) -> tuple[list[tuple[str, int]], str]:
    """
    Parse the metalink headers to get a list of caches to attempt to try in priority orider
    """
    linkPrio: list[tuple[str, int]] = []

    if "Link" in headers:
        links = headers["Link"].split(",")
        for mlink in links:
            elmts = mlink.split(";")
            mdict = {}
            for elm in elmts[1:]:
                left, right = elm.split("=", 1)
                mdict[left.strip()] = right.strip()

            priority = len(headers)
            if mdict["pri"]:
                priority = int(mdict["pri"])

            link = elmts[0].strip(" <>")

            linkPrio.append([link, priority])
    linkPrio.sort(key=lambda x: x[1])

    # Pull out the namespace information; we'll use this to populate
    # the namespace prefix cache later
    namespace = ""
    for info in headers.get("X-Pelican-Namespace", "").split(","):
        info = info.strip()
        pair = info.split("=", 1)
        if len(pair) < 2:
            continue
        key, val = pair
        if key == "namespace":
            namespace = val
            break

    return linkPrio, namespace
