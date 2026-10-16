"""Team page: companies, categories, skills, locations and to-dos."""
import sqlite3
from datetime import datetime
from flask import flash, redirect, render_template, request, session, url_for
from recruiter_ats.config import app
from recruiter_ats.database import db
from recruiter_ats.security import audit, permission_required, staff_only


@app.route("/operations")
@staff_only
def operations():
    con=db()
    data={k:con.execute(f"SELECT * FROM {k} ORDER BY name").fetchall() for k in ["companies","categories","skills","locations"]}
    con.close()
    return render_template("operations.html",data=data)

@app.route("/operations/add/<kind>",methods=["POST"])
@permission_required("manage_jobs")
def operations_add(kind):
    if kind not in {"companies","categories","skills","locations"}: return "Bad request",400
    name=request.form["name"].strip()
    con=db()
    if kind=="companies": con.execute("INSERT OR IGNORE INTO companies(name,industry,website,email,contact_person,contact_email,contact_number,categories,locations,skills,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(name,request.form.get("industry","").strip(),request.form.get("website","").strip(),request.form.get("email","").strip(),request.form.get("contact_person","").strip(),request.form.get("contact_email","").strip(),request.form.get("contact_number","").strip(),request.form.get("categories","").strip(),request.form.get("locations","").strip(),request.form.get("skills","").strip(),datetime.now().isoformat(timespec="seconds")))
    else: con.execute(f"INSERT OR IGNORE INTO {kind}(name) VALUES(?)",(name,))
    con.commit(); con.close(); return redirect(url_for("operations"))

@app.route("/operations/edit/company/<int:item_id>", methods=["GET","POST"])
@permission_required("manage_jobs")
def operations_edit_company(item_id):
    con=db()
    company=con.execute("SELECT * FROM companies WHERE id=?", (item_id,)).fetchone()
    if not company:
        con.close()
        flash("Company not found.","danger")
        return redirect(url_for("operations"))
    if request.method=="POST":
        name=request.form.get("name","").strip()
        if not name:
            con.close()
            flash("Company name is required.","danger")
            return redirect(url_for("operations_edit_company", item_id=item_id))
        con.execute(
            "UPDATE companies SET name=?,industry=?,website=?,email=?,contact_person=?,contact_email=?,contact_number=?,"
            "categories=?,locations=?,skills=? WHERE id=?",
            (name, request.form.get("industry","").strip(), request.form.get("website","").strip(),
             request.form.get("email","").strip(), request.form.get("contact_person","").strip(), request.form.get("contact_email","").strip(), request.form.get("contact_number","").strip(),
             request.form.get("categories","").strip(), request.form.get("locations","").strip(),
             request.form.get("skills","").strip(), item_id)
        )
        con.commit(); con.close()
        audit("UPDATE","COMPANY",item_id,name)
        flash("Company updated successfully.","success")
        return redirect(url_for("operations"))
    con.close()
    return render_template("company_edit.html", company=company)

@app.route("/operations/delete/<kind>/<int:item_id>",methods=["POST"])
@permission_required("manage_jobs")
def operations_delete(kind,item_id):
    if kind not in {"companies","categories","skills","locations"}: return "Bad request",400
    con=db()
    try:
        cur=con.execute(f"DELETE FROM {kind} WHERE id=?",(item_id,))
        con.commit()
        if cur.rowcount:
            audit("DELETE",kind[:-1].upper(),item_id)
            flash(f"{kind[:-1].capitalize()} deleted.","success")
        else:
            flash("Item not found.","danger")
    except sqlite3.IntegrityError:
        flash(f"Cannot delete this {kind[:-1]} — it is still linked to existing jobs.","danger")
    con.close()
    return redirect(url_for("operations"))

@app.route("/todos/add",methods=["POST"])
@staff_only
def todo_add():
    con=db(); con.execute("INSERT INTO todos(title,due_date,assigned_to,created_at) VALUES(?,?,?,?)",
                          (request.form["title"],request.form.get("due_date",""),session["user_id"],datetime.now().isoformat(timespec="seconds")))
    con.commit(); con.close(); flash("To-do added.","success"); return redirect(url_for("dashboard"))

@app.route("/todos/<int:tid>/edit",methods=["POST"])
@staff_only
def todo_edit(tid):
    title=(request.form.get("title") or "").strip()
    status=request.form.get("status") or "Open"
    if status not in ("Open","Done"): status="Open"
    if not title:
        flash("Task name is required.","danger"); return redirect(url_for("dashboard")+"?open=todos")
    con=db(); con.execute("UPDATE todos SET title=?,due_date=?,status=? WHERE id=?",
                          (title,request.form.get("due_date",""),status,tid))
    con.commit(); con.close()
    audit("UPDATE","TODO",tid)
    flash("To-do updated.","success")
    return redirect(url_for("dashboard")+"?open=todos")

@app.route("/todos/<int:tid>/delete",methods=["POST"])
@staff_only
def delete_todo(tid):
    con=db(); con.execute("DELETE FROM todos WHERE id=?",(tid,)); con.commit(); con.close()
    audit("DELETE","TODO",tid)
    flash("To-do deleted.","success")
    return redirect(url_for("dashboard"))
