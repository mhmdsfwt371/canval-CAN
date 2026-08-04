# النشر — من اللاب للسحابة

كل الخطوات دي مجانية في حدود استخدامكم.

## الشكل النهائي

```
GitHub Actions  (كل ليلة)
      │  بيسحب الكتالوج، بيمسح الأجهزة، بيبني قاعدة البيانات
      ▼
Cloud Run       (الخدمة + الواجهة)
      ▲
      │  دخول بحساب جوجل عن طريق Firebase
   الفريق
```

**قاعدة البيانات بتتبني في الليل وبتترحّل جوه الصورة.** يعني الخدمة الشغالة
مش شايلة أي بيانات دخول، ومحدش بيستنى Xirgo وهو بيسأل.

---

## قبل ما تبدأ

محتاج:
- حساب جوجل (نفس الحساب هيستخدم لـ Cloud و Firebase)
- حساب GitHub
- بيانات دخول Xirgo (`client_id` و `client_secret`)
- رمز أفاقي برو

---

## ١) ارفع الكود على GitHub

```powershell
cd D:\canval
git init
git add .
git commit -m "canval"
```

اعمل مستودع **خاص** على GitHub، وبعدين:

```powershell
git remote add origin https://github.com/<حسابك>/canval.git
git branch -M main
git push -u origin main
```

وانقل ملف المزامنة لمكانه:

```powershell
mkdir .github\workflows
copy deploy\.github\workflows\nightly.yml .github\workflows\
git add .github && git commit -m "nightly sync" && git push
```

> **مهم:** المستودع لازم يكون خاص. الكود مفيهوش أسرار، بس أي حاجة تانية
> تدخل عليه بعدين ممكن تكون حساسة.

---

## ٢) اعمل مشروع على Google Cloud

من [console.cloud.google.com](https://console.cloud.google.com):

1. **New Project** → سمّيه `canval`
2. فعّل الخدمتين دول من `APIs & Services`:
   - Cloud Run Admin API
   - Cloud Build API
3. من `Billing` اربط بطاقة — **مش هتتحاسب**، بس جوجل بتطلبها.
   حط **Budget Alert** بـ ٥ دولار عشان تطمن.

---

## ٣) اعمل Firebase للدخول

من [console.firebase.google.com](https://console.firebase.google.com):

1. **Add project** → اختار مشروع `canval` اللي عملته
2. `Authentication` → `Get started` → فعّل **Google**
3. `Authentication` → `Settings` → `Authorized domains` → ضيف نطاق
   Cloud Run بعد ما تنشر (هييجي في الخطوة ٦)
4. `Project settings` → `Your apps` → `Web` → انسخ الإعدادات

حط الإعدادات في `web/config.js`:

```javascript
window.CANVAL_FIREBASE = {
  apiKey: "AIza...",
  authDomain: "canval.firebaseapp.com",
  projectId: "canval",
  appId: "1:000...:web:000..."
};
```

> البيانات دي **مش سرية**. هي بتعرّف المشروع بس. الصلاحية بتتقرر في
> الخدمة، اللي بتتحقق من التوقيع مقابل مفاتيح جوجل المنشورة.

---

## ٤) اربط GitHub بـ Google Cloud

عشان Actions يقدر ينشر من غير ما تحط مفتاح دائم في GitHub.

في Cloud Shell (الأيقونة فوق يمين في كونسول جوجل):

```bash
PROJECT_ID=$(gcloud config get-value project)
PROJECT_NUM=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
REPO="<حسابك>/canval"

gcloud iam service-accounts create canval-deploy \
  --display-name="canval deployer"

SA="canval-deploy@$PROJECT_ID.iam.gserviceaccount.com"

for ROLE in run.admin cloudbuild.builds.editor storage.admin \
            artifactregistry.admin iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$SA" --role="roles/$ROLE" --quiet
done

gcloud iam workload-identity-pools create github \
  --location=global --display-name="GitHub"

gcloud iam workload-identity-pools providers create-oidc github \
  --location=global --workload-identity-pool=github \
  --display-name="GitHub" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='$REPO'" \
  --issuer-uri="https://token.actions.githubusercontent.com"

gcloud iam service-accounts add-iam-policy-binding $SA \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUM/locations/global/workloadIdentityPools/github/attribute.repository/$REPO"

echo
echo "GCP_WIF_PROVIDER:"
echo "projects/$PROJECT_NUM/locations/global/workloadIdentityPools/github/providers/github"
echo "GCP_SERVICE_ACCOUNT: $SA"
```

**انسخ آخر سطرين.**

---

## ٥) حط الأسرار في GitHub

في المستودع → `Settings` → `Secrets and variables` → `Actions` → `New secret`:

| الاسم | القيمة |
|---|---|
| `XDM_CLIENT_ID` | بيانات دخول Xirgo |
| `XDM_CLIENT_SECRET` | بيانات دخول Xirgo |
| `XDM_DOMAIN` | `eu` |
| `AFAQY_TOKEN` | رمز أفاقي برو |
| `FIREBASE_PROJECT` | `canval` |
| `GCP_WIF_PROVIDER` | من الخطوة ٤ |
| `GCP_SERVICE_ACCOUNT` | من الخطوة ٤ |

---

## ٦) شغّلها

في المستودع → `Actions` → `nightly sync and deploy` → `Run workflow`

هتاخد حوالي ١٠ دقايق (بتقرا ٦٤٨٣ جهاز). في آخر الصفحة هتلاقي رابط الخدمة:

```
https://canval-xxxxx-ew.a.run.app
```

**رجّع الرابط ده لـ Firebase:**
`Authentication` → `Settings` → `Authorized domains` → `Add domain`

خلاص. افتح الرابط، سجّل دخول، واضغط **"إضافة إلى الشاشة الرئيسية"** على الموبايل.

---

## بعد كده

**كل ليلة الساعة ٢** بيشتغل لوحده: بيسحب الجديد، بيمسح، وبينشر.
النتيجة بتظهر في `Actions` — بيقولك إيه اللي دخل الكتالوج.

**كل ٣٠ يوم** الرمز بتاع أفاقي بيخلص:
1. هات رمز جديد من المتصفح
2. `Settings` → `Secrets` → عدّل `AFAQY_TOKEN`
3. `Actions` → `Run workflow`

---

## التكلفة

| الخدمة | الحد المجاني | استخدامكم المتوقع |
|---|---|---|
| Cloud Run | ٢ مليون طلب/شهر | بضع مئات/يوم |
| Cloud Build | ١٢٠ دقيقة/يوم | ~٣ دقايق/يوم |
| Artifact Registry | ٠٫٥ جيجا | ~٢٠٠ ميجا |
| GitHub Actions | ٢٠٠٠ دقيقة/شهر | ~٣٠٠ دقيقة |
| Firebase Auth | ٥٠ ألف مستخدم/شهر | أقل من ٥٠ |

**صفر.** لو زاد استخدامكم عشرين ضعف، لسه صفر.

---

## حاجات اتعملت عن قصد

**قاعدة البيانات بتتبني في الليل مش عند الطلب.** الخدمة الشغالة مفيهاش أي
بيانات دخول، والسؤال بيترد عليه من قراءة محلية في ملي ثانية. لو Xirgo وقعوا،
الأداة بتفضل شغالة.

**المزامنة بترفض تنشر قاعدة ناقصة.** لو المسح رجع بأقل من ١٠٠٠ ملف أو ٥٠٠
جهاز، الوظيفة بتفشل والنسخة القديمة بتفضل شغالة. من غير الفحص ده، سحبة
نصّها فشل كانت هتنشر فوق سحبة سليمة، والأداة تبدأ تقول "معمرش اتركب"
على عربيات هي بس مقدرتش تقراها.

**`--allow-unauthenticated` مش معناها مفتوحة.** دي بتخلي المتصفح يوصل
للخدمة أصلاً، والخدمة بترفض أي طلب من غير رمز موقّع تتحقق منه بنفسها.
حماية Cloud Run نفسها مكانتش هتنفع لأنها كانت هتقفل صفحة الدخول كمان.

**التحقق من الرمز بيتم بالتوقيع الحقيقي.** لو مكتبة التشفير ناقصة، الخدمة
بترفض تشتغل بدل ما تعدّي أي حد — **فحص بيعدّي دايماً أسوأ من مفيش فحص،
لأنه شكله فحص**.
