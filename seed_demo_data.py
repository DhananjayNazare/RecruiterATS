"""Load demo candidates (default 1000) that walk through the whole hiring workflow.
Run:  python seed_demo_data.py          (adds 1000 demo candidates once; safe to re-run)
      python seed_demo_data.py 500      (a different number)
      python seed_demo_data.py --reset  (removes the demo data again)
Demo rows are tagged with the e-mail domain @demo.example so they are easy to find and remove."""
import json, random, sys
from datetime import datetime, timedelta
from recruiter_ats.database import init_db, db

DOMAIN = "@demo.example"
random.seed(42)
FIRST = ["Aarav","Vihaan","Aditya","Arjun","Rohan","Karan","Rahul","Amit","Sahil","Nikhil","Priya","Ananya","Neha","Pooja","Sneha","Kavya","Isha","Riya","Meera","Divya","Sanjay","Vikram","Manish","Deepak","Suresh","Anjali","Shreya","Tanvi","Nidhi","Swati"]
LAST = ["Sharma","Patil","Deshmukh","Kulkarni","Joshi","Gupta","Singh","Verma","Mehta","Shah","Nair","Iyer","Reddy","Rao","Khan","Chavan","Jadhav","More","Pawar","Bhosale"]
CITIES = ["Pune","Mumbai","Bengaluru","Hyderabad","Chennai","Delhi","Nagpur","Ahmedabad"]
EDU = ["B.E. Computer Engineering","B.Tech Information Technology","MCA","M.Tech Computer Science","B.Sc Computer Science","MBA (HR)","B.Com","BBA"]
SOURCES = ["Naukri","LinkedIn","Indeed","Referral","Website","Candidate Portal","Resume Screening"]
POSITIONS = [  # title, category, skills
 ("Python Developer","Engineering",["Python","Flask","Django","SQL","REST APIs","Git"]),
 ("Frontend Developer","Engineering",["JavaScript","React","HTML","CSS","TypeScript","Bootstrap"]),
 ("QA Automation Engineer","Quality",["Selenium","Python","API Testing","Postman","CI/CD","SQL"]),
 ("HR Executive","Human Resources",["Recruitment","Onboarding","Payroll","Excel","Communication","HRMS"]),
 ("Data Analyst","Analytics",["SQL","Excel","Power BI","Python","Statistics","Tableau"]),
 ("DevOps Engineer","Engineering",["AWS","Docker","Kubernetes","Linux","Jenkins","Terraform"]),
]
# share of candidates per pipeline status (scaled to the requested total; sums to 100)
FUNNEL = [("Applied",20),("Screening",12),("Accepted",12),("Hold",8),("Shortlisted",14),("Interview",16),("Offer",5),("Hired",5),("Rejected",8)]
SCORE = {"Applied":None,"Screening":None,"Accepted":(51,70),"Hold":(31,50),"Shortlisted":(71,84),"Interview":(85,96),"Offer":(88,97),"Hired":(88,98),"Rejected":(12,30)}
DECISION = {"Accepted":"Accepted","Hold":"Hold","Shortlisted":"Shortlisted","Interview":"Auto Interview","Offer":"Auto Interview","Hired":"Auto Interview","Rejected":"Rejected"}
INTERVIEWERS = ["Rohit Kulkarni","Sunita Menon","Amit Deshpande"]

def funnel_statuses(total):
    out=[s for s,k in FUNNEL for _ in range(round(k*total/100))]
    while len(out)<total: out.append("Applied")
    return out[:total]

def reset(con):
    ids=[r[0] for r in con.execute("SELECT id FROM candidates WHERE email LIKE ?",("%"+DOMAIN,))]
    for i in ids: con.execute("DELETE FROM candidates WHERE id=?",(i,))   # applications/interviews cascade
    con.execute("DELETE FROM jobs WHERE title IN (%s) AND created_by IS NULL AND description LIKE 'DEMO:%%'"%",".join("?"*len(POSITIONS)),[p[0] for p in POSITIONS])
    con.commit(); print("Removed %d demo candidates."%len(ids))

def main(total=None, reset_only=False):
    if total is None:
        nums=[int(a) for a in sys.argv[1:] if a.isdigit()]; total=nums[0] if nums else 1000
    total=max(1,min(5000,total))
    init_db(); con=db(); now=datetime.now()
    if reset_only or "--reset" in sys.argv: return reset(con)
    if con.execute("SELECT 1 FROM candidates WHERE email LIKE ? LIMIT 1",("%"+DOMAIN,)).fetchone():
        reset(con)          # replace the earlier demo set (e.g. the old 100) with the new one; real data is untouched
    ts=lambda d: d.isoformat(timespec="seconds")
    comp=[r[0] for r in con.execute("SELECT id FROM companies ORDER BY id")] or [None]
    for c in {p[1] for p in POSITIONS}: con.execute("INSERT OR IGNORE INTO categories(name) VALUES(?)",(c,))
    for c in CITIES: con.execute("INSERT OR IGNORE INTO locations(name) VALUES(?)",(c,))
    for p in POSITIONS:
        for s in p[2]: con.execute("INSERT OR IGNORE INTO skills(name) VALUES(?)",(s,))
    jobs=[]
    for n,(title,cat,sk) in enumerate(POSITIONS):
        cid=con.execute("SELECT id FROM categories WHERE name=?",(cat,)).fetchone()[0]
        lid=con.execute("SELECT id FROM locations WHERE name=?",(CITIES[n%3],)).fetchone()[0]
        jd="DEMO: %s. We need a %s with hands-on experience in %s. Work with the team, deliver quality and communicate clearly."%(title,title,", ".join(sk))
        cur=con.execute("INSERT INTO jobs(title,company_id,category_id,location_id,description,requirements,employment_type,status,jd_text,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (title,comp[n%len(comp)],cid,lid,jd,"Skills: "+", ".join(sk),"Full-time","Open",jd,ts(now-timedelta(days=40)),ts(now)))
        jobs.append((cur.lastrowid,title,sk))
    random.seed(42); statuses=funnel_statuses(total); random.shuffle(statuses)
    slot=0
    for i,status in enumerate(statuses):
        name="%s %s"%(random.choice(FIRST),random.choice(LAST))   # repeated names are fine: e-mails stay unique
        jid,title,sk=jobs[i%len(jobs)]
        email=name.lower().replace(" ",".")+str(i+1)+DOMAIN
        have=random.sample(sk,k=random.randint(3,len(sk)))+random.sample(["Communication","Teamwork","Agile"],1)
        exp=round(random.uniform(0.5,9),1); city=random.choice(CITIES)
        created=now-timedelta(days=random.randint(1,35),hours=random.randint(0,20))
        resume="%s\n%s | %s\n%s with %.1f years of experience.\nSkills: %s\nEducation: %s"%(name,email,city,title,exp,", ".join(have),random.choice(EDU))
        cc=con.execute("INSERT INTO candidates(name,email,phone,location,resume_text,skills,experience_years,education,ai_summary,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (name,email,"+91 9%09d"%random.randint(0,999999999),city,resume,", ".join(have),exp,random.choice(EDU),
             "%s has %.1f years of experience as a %s, strong in %s."%(name,exp,title,", ".join(have[:3])),ts(created),ts(created))).lastrowid
        rng=SCORE[status]; score=random.randint(*rng) if rng else None; shot=None
        if score is not None:
            miss=[s for s in sk if s not in have][:3]
            shot=json.dumps({"score":score,"decision":DECISION[status],"threshold":85,"matching_keywords":have[:5],"missing_requirements":miss,
              "why_match":["Skills overlap with the JD: "+", ".join(have[:3]),"%.1f years of relevant experience"%exp,"Education fits the role"],"summary":"Demo AI screening result."})
        aid=con.execute("INSERT INTO applications(candidate_id,job_id,status,source,match_score,ai_screening,ai_auto_processed_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (cc,jid,status,random.choice(SOURCES),score,shot,ts(created+timedelta(minutes=5)) if shot else None,ts(created),ts(created+timedelta(days=1)))).lastrowid
        if status in ("Interview","Offer","Hired"):   # interview timeline: past for Offer/Hired, upcoming for Interview
            if status=="Interview":
                d=now+timedelta(days=slot//6+1); slot+=1
                while d.weekday()>4: d+=timedelta(days=1)
                when=d.replace(hour=10+(slot%6),minute=0,second=0,microsecond=0); st="Scheduled"
            else:
                when=(now-timedelta(days=random.randint(2,14))).replace(hour=11,minute=0,second=0,microsecond=0); st="Completed"
            con.execute("INSERT INTO interviews(application_id,round_name,scheduled_at,mode,interviewer,meeting_link,meeting_provider,status,feedback) VALUES(?,?,?,?,?,?,?,?,?)",
                (aid,"Technical Round 1",when.strftime("%Y-%m-%dT%H:%M"),"Online",random.choice(INTERVIEWERS),"","Custom Meeting",st,
                 "Good problem solving and communication." if st=="Completed" else None))
    con.commit(); n=con.execute("SELECT COUNT(*) FROM candidates WHERE email LIKE ?",("%"+DOMAIN,)).fetchone()[0]
    print("Added %d demo candidates, %d positions."%(n,len(jobs)))
    for s,k in con.execute("SELECT status,COUNT(*) FROM applications GROUP BY 1"): print("  %-12s %d"%(s,k))

if __name__=="__main__": main()
