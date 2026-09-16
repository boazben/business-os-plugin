---
name: production-protection
description: הגבול האמיתי בין הקוד לבין האתר החי, על Netlify — צ'קליסט של לחיצות מייסד, ארבע בדיקות לפני הפרסום הראשון, מה devops-engineer בודק, ואיך נראה שחרור, חזרה אחורה והשקה. להשתמש כשנפתח repo ל-venture, לפני הפרסום הראשון, כשמשנים את `netlify.toml` או מוסיפים build plugin, וכל פעם שמחברים שירות שמחזיק מידע של לקוחות או כסף.
---

# הגנה על production — Netlify

**ספק האחסון: Netlify, תוכנית Personal (9$ לחודש).** החלטת מייסד 16.9.2026,
מסמך `מו״פ/החלטות/01 - בחירת ספק אחסון וענן.md` בתיקיית הפרויקט.
Vercel בחינם אוסר שימוש מסחרי; Cloudflare Pages לא נותן פרסום ידני מ-Git;
ענן גדול — אין תקרת חיוב אמיתית. **העיקרון לא תלוי בספק:** מיזוג ל-`main`
בונה גרסה שמחכה, רק המייסד מפרסם בממשק, אין במחשב טוקן או התחברות לספק,
וגרסה שלא פורסמה לא נגישה לציבור.

## למה זה קיים

ה-hook של rnd-os (`hooks/security-gate.py`) עוצר טעויות ועקיפות מוכרות. הוא
**לא** גבול אבטחה: כל הסוכנים חולקים מחשב ו-shell. הגבול האמיתי: **Claude
לא מחזיק שום הרשאה שמעלה משהו לאוויר**, והעלייה לאוויר היא לחיצה של המייסד
בממשק של Netlify, עם החשבון שלו.

## הצ'קליסט של המייסד (פעם אחת ל-repo)

1. **2FA** על GitHub ועל Netlify.
2. **הרשמה ל-Netlify עם GitHub.** בהרשאת האפליקציה של Netlify ב-GitHub —
   **Only select repositories**, רק ה-repo של האתר.
3. **Add new project → Import an existing project → GitHub → ה-repo.**
   Build command ריק, Publish directory `.` (שורש ה-repo). Deploy.
4. **Project configuration → General → Visitor access → Project visibility:
   Private** — עד ההשקה. בתוכנית Free/Personal רק בעל הצוות רואה.
5. **Deploys → Lock to stop auto publishing.** מעכשיו כל push ל-`main` נבנה
   ומחכה.
6. **Project configuration → Build & deploy → Continuous deployment →
   Branches and deploy contexts:** Branch deploys — רק ענף ה-production;
   Deploy Previews — לא לבנות pull requests.
7. **חיוב:** Personal לפני ההשקה. **Auto recharge נשאר כבוי** (ברירת המחדל) —
   כשהקרדיטים נגמרים האתר נעצר, ולא נוצר חיוב בלי גבול. מעקב שימוש בעמוד
   החיוב.
8. **GitHub → ה-repo → Settings → Actions → General: Disable actions.** אין
   secrets ב-GitHub.
9. **Claude in Chrome — בלי הרשאה ל-`app.netlify.com` ול-`github.com`.** סוכן
   בדפדפן שמחובר לחשבון שלך יכול ללחוץ Publish במקומך (security-lead: High).
10. **במחשב:** לא מתחברים ל-Netlify CLI (`netlify login`) ולא מחברים MCP של
    Netlify — לא ב-WSL ולא ב-Windows.
11. **סודות של production** (סליקה, מסד נתונים, API בתשלום) — רק ב-Environment
    variables של Netlify, מוגבלים להקשר production. בשאר ההקשרים — מפתחות
    בדיקה בלבד. לא ב-`.env`, לא ב-repo, לא ב-WSL.

## ארבע בדיקות לפני הפרסום הראשון

devops-engineer (או Claude Code בסשן פיתוח) דוחף ל-`main` commit בדיקה עם
build plugin מקומי שמדפיס **שמות** בלבד — אילו constants ומשתני סביבה
שנראים כמו הרשאה קיימים בזמן build — ואם יש כזה, מנסה קריאה **לקריאה בלבד**
ל-API של Netlify ומדפיס רק את קוד התשובה.

1. **גרסה נעולה לא ציבורית.** בעמוד הגרסה ב-Deploys מעתיקים את הכתובת
   הייחודית (`<deploy-id>--<site>.netlify.app`) ופותחים בחלון גלישה בסתר.
   **נפתח הדף — נכשל.**
2. **ה-build לא יכול לפרסם בעצמו.** ביומן ה-build: אין שם של טוקן, או
   שהקריאה החזירה 401/403. **200 — נכשל**: build plugin מתוך ה-repo מחזיק
   הרשאה ל-API, ושינוי ב-`netlify.toml` הופך לשינוי רגיש שדורש
   `security-lead` ושער נוסף.
3. **קרדיטים.** בעמוד החיוב/השימוש — האם הגרסה הנעולה ירדה 15 קרדיטים. אם כן,
   מאחדים שינויים לפני מיזוג.

ואז **בדיקת Publish:** המייסד לוחץ Publish Deploy על אותה גרסה ורואה שהאתר
התעדכן (עדיין פרטי). בסוף — commit שמסיר את ה-plugin של הבדיקה.

בדיקה שנכשלה — עוצרים ומדווחים למייסד לפני כל פרסום. החלופה במסמך ההחלטה:
Vercel Pro.

**בדיקה 4 — קישור ישיר ל-Publish.** אחרי ה-push:
`gh api repos/<owner>/<repo>/commits/<sha>/status` (קריאה בלבד). אם Netlify
מדווח שם סטטוס עם `target_url` לעמוד הגרסה בממשק הניהול — זה הקישור הישיר
למשימת ה-Publish. אם לא — עמוד הגרסאות + 7 התווים הראשונים של ה-commit.
שם הפרויקט ב-Netlify וכתובת עמוד הגרסאות נרשמים ב-README של ה-repo.

## מה devops-engineer בודק לפני כל שחרור (בלי לשנות שום הגדרה)

מריץ ומדווח את הפלט, בלי להדפיס ערך של סוד:

- אין התחברות ל-Netlify CLI: `ls ~/.config/netlify/config.json` לא קיים,
  ואם `netlify` מותקן — `netlify status` לא מחובר.
- אין טוקן בסביבה: `env | grep -ciE 'NETLIFY_AUTH_TOKEN|NETLIFY_API_TOKEN'` מחזיר 0.
- אין קבצי `.env` עם סודות ב-repo או בהיסטוריה.
- `netlify.toml` ו-build plugins: כל שינוי בהם עבר `security-lead`.

סעיף שנכשל — ממצא **High** ל-`security-lead`.

## השחרור

1. ביקורות על אותו commit, `security-lead` מאשר, מיזוג fast-forward ל-`main`.
2. Netlify בונה גרסה נעולה.
3. Claude Code מכין הכול: מחכה שהגרסה נבנתה (סטטוס ה-commit ב-GitHub, בדיקה
   4), ומוצא את הקישור הישיר. המנכ"ל פותח למייסד **משימת Publish** (חוזה
   הלוח, סעיף 9): ה-commit, מה בסיכון, והקישור.
4. **המייסד:** קישור → **Publish deploy** → מעביר את המשימה ל"הושלם".
   Claude לא מפרסם ולא לוחץ במקומו.
5. בדיקת אתר חי ב-Cowork.

**חזרה אחורה:** Deploys → גרסה קודמת שעבדה → Publish deploy. מיידי, בלי
בנייה. אחר כך משימת תיקון.

**השקה (פעם אחת):** משימת ה-Publish הראשונה כוללת גם **Project visibility →
Public**. לפני זה בדיקת האתר החי לא יכולה לרוץ מבחוץ.

## מה נשאר פתוח

- **push ל-`main` לא חסום ב-GitHub** (הגנת ענפים דורשת Pro). הגרסה לא עולה
  לאוויר בלי Publish, אבל ה-repo עצמו יכול להשתנות.
- **פרויקט פרטי לא מקבל webhooks** (למשל מסולק). בשלב של סליקה ה-production
  כבר ציבורי.
- **PoP של Netlify בישראל** לא אומת.
