# Deploying MediaPull to Render

This guide provides step-by-step instructions on how to deploy the MediaPull application to [Render](https://render.com), a fantastic platform that fully supports WebSockets and background tasks (unlike serverless platforms like Vercel).

## Prerequisites
1. A [GitHub](https://github.com) account.
2. A free [Render](https://render.com) account.

---

## Step 1: Push Your Code to GitHub
Render deploys your application directly from a GitHub repository.

1. Go to GitHub and create a new, empty repository.
2. Open your terminal in the `MediaPull-main` directory and run the following commands to initialize Git and push your code:

```bash
git init
git add .
git commit -m "Initial commit for Render deployment"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPOSITORY_NAME.git
git push -u origin main
```
*(Replace `YOUR_USERNAME` and `YOUR_REPOSITORY_NAME` with your actual GitHub details).*

---

## Step 2: Create a Web Service on Render

We have already included a `render.yaml` file in this project, which acts as a "Blueprint" for Render. This means Render will automatically know how to build and start your application!

1. Log into your Render dashboard.
2. Click the **"New +"** button in the top right corner.
3. Select **Blueprint** from the dropdown menu.
4. Connect your GitHub account if you haven't already.
5. Find and select the repository you created in Step 1.
6. Click **Apply**.

Render will automatically read the `render.yaml` file, provision a server with Python 3.10, install your dependencies via `requirements.txt`, and start the app using `gunicorn`.

---

## Step 3: Configure Environment Variables (Optional but Recommended)

In the `render.yaml` file, we already configured Render to auto-generate a `SECRET_KEY` for you. This secures your users' sessions. 

If you want to manually set a specific secret key or add other variables in the future:
1. Go to your Web Service on the Render dashboard.
2. Click on **Environment** in the left sidebar.
3. Add a new Environment Variable.
   - **Key:** `SECRET_KEY`
   - **Value:** *(Paste a long, random string of characters)*

---

## Understanding How MediaPull Works on Render

### WebSockets
Render natively supports persistent connections. The real-time progress bars and UI updates handled by WebSockets will work flawlessly without any extra configuration.

### Background Downloads
When a user downloads a video, the file is temporarily saved to `~/Downloads/MediaPull` inside the Render container. 
- **Important Note:** Render's free tier uses an *ephemeral file system*. This means that whenever your app restarts or redeploys, any downloaded videos currently sitting on the server will be deleted. 
- This is perfectly fine for MediaPull, as the files are only needed temporarily while the user downloads them through the browser!

---

## Troubleshooting

- **App goes to sleep:** On Render's Free tier, your web service will go to sleep after 15 minutes of inactivity. The next time someone opens your app, it will take about 30-50 seconds to wake up. To avoid this, you can upgrade to Render's starter tier ($7/month).
- **Rate Limits:** MediaPull has built-in rate limiters. If you suddenly get blocked during testing, you've likely hit the 100 requests/hour limit defined in `app.py`.
